"""Herdr allowlist egress proxy with per-task request budget.

Stdlib-only HTTP forward proxy for Firejail-sandboxed Herdr agents.

- Listens on 127.0.0.1:<port> (localhost only; port comes from the env template).
- Plain HTTP: client sends `GET http://host/path` to the proxy.
- HTTPS: client sends `CONNECT host:443`, then tunnels TLS bytes.
- Each request checks:
    1. host against allowlist.txt (exact or `*.example.com`)
    2. BUDGET_ID usage < BUDGET_MAX (persisted JSON counters)
- Denied: 403 domain-not-allowed, 429 budget-exhausted.
- Every decision appended as JSONL to access.log.

Budget identity: the wrapper encodes the task's BUDGET_ID as the userinfo
part of the proxy URL (`http://<budget-id>:<secret>@127.0.0.1:<port>`), so
stock tools (curl, git, pip, npm) send it as `Proxy-Authorization: Basic ...`
on every request — including CONNECT, which cannot carry custom headers.
The proxy decodes that first, then falls back to the `X-Budget-Id` header
(plain HTTP only), then to the last-seen ID from that client IP, then to
the proxy default (--budget-id / $BUDGET_ID). Run one proxy instance per
env template (each template has its own loopback port, allowlist and
budget default); tasks sharing a template are still accounted separately
via their BUDGET_ID. For strongest isolation run one proxy per task with
--budget-id fixed.

Per-task caps: `herdr-agent-firejail` registers each task (id, max, secret)
in `<state-dir>/tasks.json` at launch. A registered task is enforced at its
own max (the wrapper --budget-max is real, not advisory) and must present
the matching secret; a wrong/missing secret is denied as 403
budget-auth-failed without consuming budget. Unregistered IDs fall back to
the proxy instance --budget-max with no auth check (previous behavior).
The registry reloads automatically (mtime check), so no proxy restart is
needed after launching a task. Counters in budget.json are updated under an
exclusive flock, so several template proxies may share one state dir.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import http.client
import json
import os
import socket
import socketserver
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

try:
    import fcntl
except ImportError:  # pragma: no cover — non-Unix platforms have no flock
    fcntl = None  # type: ignore[assignment]

DEFAULT_PORT = 8888
DEFAULT_ALLOWLIST = Path(__file__).resolve().parent.parent / "config" / "allowlist.txt"
DEFAULT_STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "herdr-web-proxy"


def load_allowlist(path: Path) -> list[str]:
    patterns: list[str] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line.lower())
    return patterns


def host_allowed(host: str, patterns: list[str]) -> bool:
    host = host.lower().split(":")[0].strip().rstrip(".")
    for pat in patterns:
        pat = pat.strip().lower()
        if pat.startswith("*."):
            suffix = pat[1:]  # ".example.com"
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif fnmatch.fnmatchcase(host, pat):
            return True
    return False


def _lock_ex(f) -> None:
    if fcntl is not None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)


class BudgetStore:
    """Thread- and process-safe persisted per-BUDGET_ID counters.

    Mutations do read-modify-write under an exclusive `flock` on the state
    file, so several proxy instances (one per env template) can share a
    state dir without losing counts. A `threading.Lock` additionally
    serializes threads within one process.
    """

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.file = self.state_dir / "budget.json"
        self.lock = threading.Lock()
        self.counts: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.file.read_text())
            self.counts = data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self.counts = {}

    @staticmethod
    def _read_counts(f) -> dict:
        try:
            f.seek(0)
            data = json.loads(f.read() or "{}")
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError, ValueError):
            return {}

    @staticmethod
    def _write_counts(f, counts: dict) -> None:
        f.seek(0)
        f.truncate()
        f.write(json.dumps(counts, indent=2))
        f.flush()

    @staticmethod
    def _used(counts: dict, budget_id: str) -> int:
        try:
            return int(counts.get(budget_id, 0))
        except (TypeError, ValueError):
            return 0

    def check_and_consume(self, budget_id: str, budget_max: int) -> tuple[bool, int]:
        """Return (allowed, current_count_after_consume_or_current)."""
        with self.lock:
            with open(self.file, "a+") as f:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                counts = self._read_counts(f)
                used = self._used(counts, budget_id)
                if used >= budget_max:
                    self.counts = counts
                    return False, used
                counts[budget_id] = used + 1
                self._write_counts(f, counts)
                self.counts = counts
                return True, used + 1

    def reset(self, budget_id: str) -> None:
        with self.lock:
            with open(self.file, "a+") as f:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                counts = self._read_counts(f)
                counts.pop(budget_id, None)
                self._write_counts(f, counts)
                self.counts = counts


class TaskRegistry:
    """Per-task budget caps + auth secrets (`tasks.json` in the state dir).

    Written by `herdr-agent-firejail` at launch (one entry per task:
    `{budget_id: {"max": int, "secret": str}}`); read by the proxy.
    Entries reload automatically when the file mtime changes, so launching
    a task needs no proxy restart. Malformed entries are ignored.
    """

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        self._mtime: float | None = None
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            with self.lock:
                self._entries = {}
                self._mtime = None
            return
        with self.lock:
            if not force and self._mtime == mtime:
                return
            try:
                raw = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError, ValueError):
                raw = {}
            entries: dict[str, dict] = {}
            if isinstance(raw, dict):
                for bid, ent in raw.items():
                    if (isinstance(ent, dict) and isinstance(ent.get("max"), int)
                            and not isinstance(ent.get("max"), bool) and ent["max"] > 0
                            and isinstance(ent.get("secret"), str) and ent["secret"]):
                        entries[str(bid)] = {"max": ent["max"], "secret": ent["secret"]}
            self._entries = entries
            self._mtime = mtime

    def get(self, budget_id: str) -> dict | None:
        self.refresh()
        with self.lock:
            ent = self._entries.get(budget_id)
            return dict(ent) if ent else None

    def register(self, budget_id: str, max: int, secret: str) -> None:
        """Create/update one task entry (called by the launch wrapper)."""
        if not budget_id or not isinstance(max, int) or isinstance(max, bool) \
                or max <= 0 or not secret:
            raise ValueError("register needs a non-empty id, positive max and non-empty secret")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            with open(self.path, "a+") as f:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.seek(0)
                    raw = json.loads(f.read() or "{}")
                except (json.JSONDecodeError, OSError, ValueError):
                    raw = {}
                if not isinstance(raw, dict):
                    raw = {}
                raw[str(budget_id)] = {"max": max, "secret": secret}
                f.seek(0)
                f.truncate()
                f.write(json.dumps(raw, indent=2))
                f.flush()
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        self.refresh(force=True)

    def revoke(self, budget_id: str) -> bool:
        """Drop one task entry. Returns True if an entry existed."""
        with self.lock:
            with open(self.path, "a+") as f:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.seek(0)
                    raw = json.loads(f.read()) if self.path.exists() else {}
                except (json.JSONDecodeError, OSError, ValueError):
                    raw = {}
                if not isinstance(raw, dict) or budget_id not in raw:
                    if fcntl is not None:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    return False
                del raw[budget_id]
                f.seek(0)
                f.truncate()
                f.write(json.dumps(raw, indent=2))
                f.flush()
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        self.refresh(force=True)
        return True


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "HerdrWebProxy/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet; we log JSONL ourselves
        pass

    # -- helpers ---------------------------------------------------------
    @property
    def proxy(self) -> "ProxyServer":
        return self.server  # type: ignore[return-value]

    def _budget_identity(self) -> tuple[str, str | None]:
        # 1) Proxy-Authorization userinfo (works for GET *and* CONNECT;
        #    the wrapper puts `BUDGET_ID:SECRET` in the proxy URL userinfo part).
        auth = self.headers.get("Proxy-Authorization", "")
        if auth.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(auth.split(None, 1)[1]).decode("utf-8", "replace")
                userinfo = decoded.split(":", 1)
                candidate = urllib.parse.unquote(userinfo[0]).strip()
                secret = urllib.parse.unquote(userinfo[1]).strip() \
                    if len(userinfo) > 1 else None
                if candidate:
                    self.proxy.last_budget_by_ip[self.client_address[0]] = candidate
                    return candidate, (secret or None)
            except Exception:  # noqa: BLE001 — fall through to other sources
                pass
        hdr = self.headers.get("X-Budget-Id")
        if hdr:
            hdr = hdr.strip()
            self.proxy.last_budget_by_ip[self.client_address[0]] = hdr
            return hdr, None
        return self.proxy.last_budget_by_ip.get(
            self.client_address[0], self.proxy.default_budget_id
        ), None

    def _task_policy(self, budget_id: str, secret: str | None) -> tuple[int, str]:
        """Return (effective_max, auth_state: ok|failed|none).

        Registered tasks are capped at their own max and must present the
        matching secret. Unregistered IDs keep the previous behavior:
        proxy-instance max, no auth check.
        """
        entry = self.proxy.tasks.get(budget_id) if self.proxy.tasks else None
        if entry is None:
            return self.proxy.budget_max, "none"
        if secret and secret == entry["secret"]:
            return entry["max"], "ok"
        return entry["max"], "failed"

    def _log(self, **fields):
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}
        with self.proxy.log_lock:
            with open(self.proxy.access_log, "a") as f:
                f.write(json.dumps(rec) + "\n")

    def _deny(self, code: int, reason: str, host: str, budget_id: str, count: int,
              auth: str = "none"):
        body = f"{code} {reason} (host={host} budget={budget_id} used={count})\n".encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self._log(host=host, decision=reason, budget_id=budget_id, count=count,
                  method=self.command, code=code, auth=auth)
        self.wfile.write(body)

    # -- plain HTTP forwarding -------------------------------------------
    def do_GET(self): return self._forward()
    def do_POST(self): return self._forward()
    def do_PUT(self): return self._forward()
    def do_DELETE(self): return self._forward()
    def do_PATCH(self): return self._forward()
    def do_HEAD(self): return self._forward()
    def do_OPTIONS(self): return self._forward()

    def _forward(self):
        budget_id, secret = self._budget_identity()
        parts = urlsplit(self.path)
        host = parts.hostname or self.headers.get("Host", "")
        if not host:
            self.send_error(400, "absolute URI required")
            return
        if not host_allowed(host, self.proxy.allowlist):
            self._deny(403, "domain-not-allowed", host, budget_id,
                       self.proxy.budgets.counts.get(budget_id, 0))
            return
        task_max, auth = self._task_policy(budget_id, secret)
        if auth == "failed":
            self._deny(403, "budget-auth-failed", host, budget_id,
                       self.proxy.budgets.counts.get(budget_id, 0), auth=auth)
            return
        ok, count = self.proxy.budgets.check_and_consume(budget_id, task_max)
        if not ok:
            self._deny(429, "budget-exhausted", host, budget_id, count, auth=auth)
            return
        port = parts.port or 80
        origin = parts.path or "/"
        if parts.query:
            origin += "?" + parts.query
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        try:
            conn = http.client.HTTPConnection(host, port, timeout=20)
            fwd = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("proxy-connection", "proxy-authorization",
                                        "connection", "x-budget-id")}
            fwd["Connection"] = "close"
            conn.request(self.command, origin, body=body, headers=fwd)
            resp = conn.getresponse()
            data = resp.read()
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() in ("transfer-encoding", "connection"):
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self._log(host=host, decision="allow", budget_id=budget_id,
                      count=count, method=self.command, code=resp.status, auth=auth)
            self.wfile.write(data)
        except Exception as e:  # noqa: BLE001
            self._deny(502, f"upstream-error: {e}", host, budget_id, count, auth=auth)

    # -- HTTPS tunneling ---------------------------------------------------
    def do_CONNECT(self):
        budget_id, secret = self._budget_identity()
        hostport = self.path
        host = hostport.split(":")[0]
        try:
            port = int(hostport.split(":")[1]) if ":" in hostport else 443
        except ValueError:
            self.send_error(400, "bad CONNECT target")
            return
        if not host_allowed(host, self.proxy.allowlist):
            self._deny(403, "domain-not-allowed", host, budget_id,
                       self.proxy.budgets.counts.get(budget_id, 0))
            return
        task_max, auth = self._task_policy(budget_id, secret)
        if auth == "failed":
            self._deny(403, "budget-auth-failed", host, budget_id,
                       self.proxy.budgets.counts.get(budget_id, 0), auth=auth)
            return
        ok, count = self.proxy.budgets.check_and_consume(budget_id, task_max)
        if not ok:
            self._deny(429, "budget-exhausted", host, budget_id, count, auth=auth)
            return
        try:
            upstream = socket.create_connection((host, port), timeout=20)
        except Exception as e:  # noqa: BLE001
            self._deny(502, f"upstream-error: {e}", host, budget_id, count, auth=auth)
            return
        self.send_response(200, "Connection established")
        self.send_header("Connection", "close")
        self.end_headers()
        self._log(host=host, decision="allow", budget_id=budget_id,
                  count=count, method="CONNECT", code=200, auth=auth)
        self._tunnel(self.connection, upstream)

    @staticmethod
    def _tunnel(a: socket.socket, b: socket.socket):
        def pipe(src, dst):
            try:
                while True:
                    chunk = src.recv(65536)
                    if not chunk:
                        break
                    dst.sendall(chunk)
            except OSError:
                pass
            finally:
                for s in (a, b):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
        t1 = threading.Thread(target=pipe, args=(a, b), daemon=True)
        t2 = threading.Thread(target=pipe, args=(b, a), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        a.close()
        b.close()


class ProxyServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, *, allowlist, budgets,
                  default_budget_id, budget_max, access_log, tasks=None):
        super().__init__(addr, handler)
        self.allowlist = allowlist
        self.budgets = budgets
        self.default_budget_id = default_budget_id
        self.budget_max = budget_max
        self.access_log = access_log
        self.tasks = tasks
        self.log_lock = threading.Lock()
        self.last_budget_by_ip: dict[str, str] = {}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Herdr allowlist egress proxy")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PROXY_PORT", DEFAULT_PORT)))
    ap.add_argument("--allowlist", type=Path, default=Path(os.environ.get("ALLOWLIST", DEFAULT_ALLOWLIST)))
    ap.add_argument("--state-dir", type=Path, default=Path(os.environ.get("STATE_DIR", DEFAULT_STATE_DIR)))
    ap.add_argument("--budget-id", default=os.environ.get("BUDGET_ID", "default"))
    ap.add_argument("--budget-max", type=int, default=int(os.environ.get("BUDGET_MAX", 200)))
    ap.add_argument("--reset", metavar="BUDGET_ID", help="reset counter for ID and exit")
    ap.add_argument("--revoke", metavar="BUDGET_ID",
                    help="drop task registry entry (and counter) for ID and exit")
    args = ap.parse_args(argv)

    budgets = BudgetStore(args.state_dir)
    if args.reset:
        budgets.reset(args.reset)
        print(f"reset {args.reset}")
        return 0
    if args.revoke:
        revoked = TaskRegistry(args.state_dir / "tasks.json").revoke(args.revoke)
        budgets.reset(args.revoke)
        print(f"revoked {args.revoke}" if revoked else f"no registry entry for {args.revoke}")
        return 0

    patterns = load_allowlist(args.allowlist)
    registry = TaskRegistry(args.state_dir / "tasks.json")
    server = ProxyServer(("127.0.0.1", args.port), ProxyHandler,
                         allowlist=patterns, budgets=budgets,
                         default_budget_id=args.budget_id,
                         budget_max=args.budget_max,
                         access_log=args.state_dir / "access.log",
                         tasks=registry)
    print(f"proxy on 127.0.0.1:{args.port} allowlist={args.allowlist} "
          f"budget_id={args.budget_id} max={args.budget_max} state={args.state_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
