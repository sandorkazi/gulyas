"""Herdr allowlist egress proxy with per-task request budget.

Stdlib-only HTTP forward proxy for Firejail-sandboxed Herdr agents.

- Listens on 127.0.0.1:8888 (localhost only).
- Plain HTTP: client sends `GET http://host/path` to the proxy.
- HTTPS: client sends `CONNECT host:443`, then tunnels TLS bytes.
- Each request checks:
    1. host against allowlist.txt (exact or `*.example.com`)
    2. BUDGET_ID usage < BUDGET_MAX (persisted JSON counters)
- Denied: 403 domain-not-allowed, 429 budget-exhausted.
- Every decision appended as JSONL to access.log.

Budget identity: client env BUDGET_ID is conveyed per request. For plain HTTP
we read the `X-Budget-Id` header if present, else the proxy default
(--budget-id / $BUDGET_ID). For CONNECT there are no custom headers, so the
*last seen* X-Budget-Id from that client IP is reused, falling back to the
default. In practice: one proxy per host, agents set BUDGET_ID env and send
one plain-HTTP request first, or run one proxy instance per task with
--budget-id fixed (see docker compose / setup.sh).
"""

from __future__ import annotations

import argparse
import fnmatch
import http.client
import json
import os
import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

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


class BudgetStore:
    """Thread-safe persisted per-BUDGET_ID counters."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.file = self.state_dir / "budget.json"
        self.lock = threading.Lock()
        self.counts: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        try:
            self.counts = json.loads(self.file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.counts = {}

    def _save(self) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.counts, indent=2))
        tmp.replace(self.file)

    def check_and_consume(self, budget_id: str, budget_max: int) -> tuple[bool, int]:
        """Return (allowed, current_count_after_consume_or_current)."""
        with self.lock:
            used = int(self.counts.get(budget_id, 0))
            if used >= budget_max:
                return False, used
            self.counts[budget_id] = used + 1
            self._save()
            return True, used + 1

    def reset(self, budget_id: str) -> None:
        with self.lock:
            self.counts.pop(budget_id, None)
            self._save()


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "HerdrWebProxy/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet; we log JSONL ourselves
        pass

    # -- helpers ---------------------------------------------------------
    @property
    def proxy(self) -> "ProxyServer":
        return self.server  # type: ignore[return-value]

    def _budget_id(self) -> str:
        hdr = self.headers.get("X-Budget-Id")
        if hdr:
            hdr = hdr.strip()
            self.proxy.last_budget_by_ip[self.client_address[0]] = hdr
            return hdr
        return self.proxy.last_budget_by_ip.get(
            self.client_address[0], self.proxy.default_budget_id
        )

    def _log(self, **fields):
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}
        with self.proxy.log_lock:
            with open(self.proxy.access_log, "a") as f:
                f.write(json.dumps(rec) + "\n")

    def _deny(self, code: int, reason: str, host: str, budget_id: str, count: int):
        body = f"{code} {reason} (host={host} budget={budget_id} used={count})\n".encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self._log(host=host, decision=reason, budget_id=budget_id, count=count,
                  method=self.command, code=code)

    # -- plain HTTP forwarding -------------------------------------------
    def do_GET(self): return self._forward()
    def do_POST(self): return self._forward()
    def do_PUT(self): return self._forward()
    def do_DELETE(self): return self._forward()
    def do_PATCH(self): return self._forward()
    def do_HEAD(self): return self._forward()
    def do_OPTIONS(self): return self._forward()

    def _forward(self):
        budget_id = self._budget_id()
        parts = urlsplit(self.path)
        host = parts.hostname or self.headers.get("Host", "")
        if not host:
            self.send_error(400, "absolute URI required")
            return
        if not host_allowed(host, self.proxy.allowlist):
            self._deny(403, "domain-not-allowed", host, budget_id,
                       self.proxy.budgets.counts.get(budget_id, 0))
            return
        ok, count = self.proxy.budgets.check_and_consume(budget_id, self.proxy.budget_max)
        if not ok:
            self._deny(429, "budget-exhausted", host, budget_id, count)
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
                   if k.lower() not in ("proxy-connection", "connection", "x-budget-id")}
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
            self.wfile.write(data)
            self._log(host=host, decision="allow", budget_id=budget_id,
                      count=count, method=self.command, code=resp.status)
        except Exception as e:  # noqa: BLE001
            self._deny(502, f"upstream-error: {e}", host, budget_id, count)

    # -- HTTPS tunneling ---------------------------------------------------
    def do_CONNECT(self):
        budget_id = self._budget_id()
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
        ok, count = self.proxy.budgets.check_and_consume(budget_id, self.proxy.budget_max)
        if not ok:
            self._deny(429, "budget-exhausted", host, budget_id, count)
            return
        try:
            upstream = socket.create_connection((host, port), timeout=20)
        except Exception as e:  # noqa: BLE001
            self._deny(502, f"upstream-error: {e}", host, budget_id, count)
            return
        self.send_response(200, "Connection established")
        self.send_header("Connection", "close")
        self.end_headers()
        self._log(host=host, decision="allow", budget_id=budget_id,
                  count=count, method="CONNECT", code=200)
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
                 default_budget_id, budget_max, access_log):
        super().__init__(addr, handler)
        self.allowlist = allowlist
        self.budgets = budgets
        self.default_budget_id = default_budget_id
        self.budget_max = budget_max
        self.access_log = access_log
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
    args = ap.parse_args(argv)

    budgets = BudgetStore(args.state_dir)
    if args.reset:
        budgets.reset(args.reset)
        print(f"reset {args.reset}")
        return 0

    patterns = load_allowlist(args.allowlist)
    server = ProxyServer(("127.0.0.1", args.port), ProxyHandler,
                         allowlist=patterns, budgets=budgets,
                         default_budget_id=args.budget_id,
                         budget_max=args.budget_max,
                         access_log=args.state_dir / "access.log")
    print(f"proxy on 127.0.0.1:{args.port} allowlist={args.allowlist} "
          f"budget_id={args.budget_id} max={args.budget_max} state={args.state_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
