"""Unit + live-localhost tests for allowlist matching, budget store and proxy."""

import base64
import http.client
import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from herdr_web_proxy import BudgetStore, ProxyServer, ProxyHandler, TaskRegistry, host_allowed, main  # noqa: E402
from lib_env_template import is_valid_domain_pattern  # noqa: E402


def test_host_allowed_exact_and_wildcard():
    patterns = ["github.com", "*.githubusercontent.com"]
    assert host_allowed("github.com", patterns)
    assert host_allowed("GITHUB.COM", patterns)
    assert host_allowed("raw.githubusercontent.com", patterns)
    assert not host_allowed("githubusercontent.com", patterns)  # bare domain not matched by '*.'
    assert not host_allowed("evil.example", patterns)


def test_host_allowed_strips_port_and_trailing_dot():
    assert host_allowed("github.com:443", ["github.com"])
    assert host_allowed("github.com.", ["github.com"])
    assert not host_allowed("", ["github.com"])


def test_domain_pattern_validation_rejects_overbroad():
    for bad in ["*", "*.", "**.example.com", "https://a.com", "a.com:443",
                "a/b.com", "has space.com", "UPPER.COM", ""]:
        assert not is_valid_domain_pattern(bad), bad
    for good in ["github.com", "*.githubusercontent.com", "a-b.c123.example"]:
        assert is_valid_domain_pattern(good), good


def test_budget_store_counts_and_reset(tmp_path):
    store = BudgetStore(tmp_path)
    ok, count = store.check_and_consume("task-a", 2)
    assert (ok, count) == (True, 1)
    ok, count = store.check_and_consume("task-a", 2)
    assert (ok, count) == (True, 2)
    ok, count = store.check_and_consume("task-a", 2)
    assert ok is False and count == 2
    store.reset("task-a")
    ok, count = store.check_and_consume("task-a", 2)
    assert (ok, count) == (True, 1)


class _Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = b"upstream-ok"
        # record whether budget creds leaked upstream (must not)
        self.server.seen_auth = self.headers.get("Proxy-Authorization")
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start(server):
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


def _proxy(tmp_path, allowlist, budget_max=10, default_budget="default", tasks=None):
    state = tmp_path / "pstate"
    state.mkdir(exist_ok=True)
    budgets = BudgetStore(state)
    srv = ProxyServer(("127.0.0.1", 0), ProxyHandler,
                      allowlist=allowlist, budgets=budgets,
                      default_budget_id=default_budget,
                      budget_max=budget_max,
                      access_log=state / "access.log",
                      tasks=tasks)
    _start(srv)
    return srv


def _auth(user, secret=None):
    creds = base64.b64encode(f"{user}:{secret or ''}".encode()).decode()
    return {"Proxy-Authorization": f"Basic {creds}"}


def _get_via_proxy(proxy_port, host, port, path="/", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=10)
    conn.request("GET", f"http://{host}:{port}{path}", headers=headers or {})
    return conn.getresponse()


def test_proxy_forwards_allowed_and_strips_auth(tmp_path):
    up = HTTPServer(("127.0.0.1", 0), _Upstream)
    up.seen_auth = "unset"
    _start(up)
    uport = up.server_address[1]
    px = _proxy(tmp_path, ["127.0.0.1"], budget_max=10)
    pport = px.server_address[1]
    try:
        creds = base64.b64encode(b"task-1:").decode()
        resp = _get_via_proxy(pport, "127.0.0.1", uport,
                              headers={"Proxy-Authorization": f"Basic {creds}"})
        assert resp.status == 200
        assert resp.read() == b"upstream-ok"
        assert up.seen_auth is None  # stripped before forwarding
        log = (tmp_path / "pstate" / "access.log").read_text()
        assert '"task-1"' in log and "allow" in log
    finally:
        px.shutdown()
        up.shutdown()


def test_proxy_denies_unknown_domain_403(tmp_path):
    px = _proxy(tmp_path, ["github.com"], budget_max=10)
    pport = px.server_address[1]
    try:
        resp = _get_via_proxy(pport, "evil.example", 80)
        assert resp.status == 403
        assert b"domain-not-allowed" in resp.read()
    finally:
        px.shutdown()


def test_proxy_budget_exhaustion_429_and_reset(tmp_path):
    up = HTTPServer(("127.0.0.1", 0), _Upstream)
    _start(up)
    uport = up.server_address[1]
    px = _proxy(tmp_path, ["127.0.0.1"], budget_max=2)
    pport = px.server_address[1]
    try:
        hdr = {"X-Budget-Id": "job-x"}
        assert _get_via_proxy(pport, "127.0.0.1", uport, headers=hdr).status == 200
        assert _get_via_proxy(pport, "127.0.0.1", uport, headers=hdr).status == 200
        r3 = _get_via_proxy(pport, "127.0.0.1", uport, headers=hdr)
        assert r3.status == 429
        assert b"budget-exhausted" in r3.read()
        px.budgets.reset("job-x")
        assert _get_via_proxy(pport, "127.0.0.1", uport, headers=hdr).status == 200
    finally:
        px.shutdown()
        up.shutdown()


def test_proxy_budget_identity_prefers_auth_over_header(tmp_path):
    up = HTTPServer(("127.0.0.1", 0), _Upstream)
    _start(up)
    uport = up.server_address[1]
    px = _proxy(tmp_path, ["127.0.0.1"], budget_max=1)
    pport = px.server_address[1]
    try:
        creds = base64.b64encode(b"auth-task:").decode()
        hdr = {"Proxy-Authorization": f"Basic {creds}", "X-Budget-Id": "header-task"}
        assert _get_via_proxy(pport, "127.0.0.1", uport, headers=hdr).status == 200
        # auth-task budget now exhausted; header-task untouched
        assert _get_via_proxy(pport, "127.0.0.1", uport, headers=hdr).status == 429
        log = (tmp_path / "pstate" / "access.log").read_text()
        assert "auth-task" in log
        # a different task via userinfo-style encoded id still has budget
        other = base64.b64encode(
            (urllib.parse.quote("task a/b", safe="") + ":").encode()).decode()
        r = _get_via_proxy(pport, "127.0.0.1", uport,
                           headers={"Proxy-Authorization": f"Basic {other}"})
        assert r.status == 200
    finally:
        px.shutdown()
        up.shutdown()


def test_proxy_logs_decisions_jsonl(tmp_path):
    px = _proxy(tmp_path, ["github.com"], budget_max=10)
    pport = px.server_address[1]
    try:
        _get_via_proxy(pport, "evil.example", 80).read()
        lines = (tmp_path / "pstate" / "access.log").read_text().strip().splitlines()
        rec = json.loads(lines[-1])
        assert rec["decision"] == "domain-not-allowed"
        assert rec["code"] == 403
    finally:
        px.shutdown()


def test_registry_register_get_revoke_roundtrip(tmp_path):
    reg = TaskRegistry(tmp_path / "tasks.json")
    assert reg.get("t1") is None
    reg.register("t1", 7, "s3cr3t")
    assert reg.get("t1") == {"max": 7, "secret": "s3cr3t"}
    reg.register("t1", 9, "new")  # update keeps other entries
    reg.register("t2", 3, "s2")
    assert reg.get("t1") == {"max": 9, "secret": "new"}
    assert reg.get("t2") == {"max": 3, "secret": "s2"}
    assert reg.revoke("t1") is True
    assert reg.get("t1") is None and reg.get("t2") is not None
    assert reg.revoke("t1") is False
    for bad in [( "", 1, "s"), ("x", 0, "s"), ("x", 1, "")]:
        try:
            reg.register(*bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"register accepted {bad!r}")


def test_proxy_enforces_per_task_max(tmp_path):
    up = HTTPServer(("127.0.0.1", 0), _Upstream)
    _start(up)
    uport = up.server_address[1]
    reg = TaskRegistry(tmp_path / "tasks.json")
    reg.register("small", 1, "s-small")
    reg.register("big", 10, "s-big")
    px = _proxy(tmp_path, ["127.0.0.1"], budget_max=100, tasks=reg)
    pport = px.server_address[1]
    try:
        assert _get_via_proxy(pport, "127.0.0.1", uport,
                              headers=_auth("small", "s-small")).status == 200
        r = _get_via_proxy(pport, "127.0.0.1", uport, headers=_auth("small", "s-small"))
        assert r.status == 429 and b"budget-exhausted" in r.read()
        # big task unaffected by small's exhaustion; unregistered id uses instance max
        assert _get_via_proxy(pport, "127.0.0.1", uport,
                              headers=_auth("big", "s-big")).status == 200
        assert _get_via_proxy(pport, "127.0.0.1", uport,
                              headers=_auth("plain", "")).status == 200
    finally:
        px.shutdown()
        up.shutdown()


def test_proxy_rejects_wrong_or_missing_secret(tmp_path):
    up = HTTPServer(("127.0.0.1", 0), _Upstream)
    _start(up)
    uport = up.server_address[1]
    reg = TaskRegistry(tmp_path / "tasks.json")
    reg.register("locked", 5, "correct")
    px = _proxy(tmp_path, ["127.0.0.1"], budget_max=100, tasks=reg)
    pport = px.server_address[1]
    try:
        r = _get_via_proxy(pport, "127.0.0.1", uport, headers=_auth("locked", "wrong"))
        assert r.status == 403 and b"budget-auth-failed" in r.read()
        r = _get_via_proxy(pport, "127.0.0.1", uport, headers={"X-Budget-Id": "locked"})
        assert r.status == 403 and b"budget-auth-failed" in r.read()
        # failed attempts consume nothing: correct secret still has full budget
        for _ in range(5):
            assert _get_via_proxy(pport, "127.0.0.1", uport,
                                  headers=_auth("locked", "correct")).status == 200
        r = _get_via_proxy(pport, "127.0.0.1", uport, headers=_auth("locked", "correct"))
        assert r.status == 429
    finally:
        px.shutdown()
        up.shutdown()


def test_proxy_picks_up_registry_without_restart(tmp_path):
    up = HTTPServer(("127.0.0.1", 0), _Upstream)
    _start(up)
    uport = up.server_address[1]
    reg = TaskRegistry(tmp_path / "tasks.json")
    px = _proxy(tmp_path, ["127.0.0.1"], budget_max=100, tasks=reg)
    pport = px.server_address[1]
    try:
        # unregistered: instance default applies
        assert _get_via_proxy(pport, "127.0.0.1", uport,
                              headers=_auth("late", "")).status == 200
        reg.register("late", 1, "s-late")  # already spent 1
        r = _get_via_proxy(pport, "127.0.0.1", uport, headers=_auth("late", "s-late"))
        assert r.status == 429  # per-task max now enforced, no restart
    finally:
        px.shutdown()
        up.shutdown()


def test_budget_store_shared_file_no_lost_updates(tmp_path):
    a = BudgetStore(tmp_path)
    b = BudgetStore(tmp_path)  # second handle, as a second proxy process would hold
    errors = []

    def hammer(store, n):
        try:
            for _ in range(n):
                store.check_and_consume("shared", 1000)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(s, 25)) for s in (a, b) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    fresh = BudgetStore(tmp_path)
    assert fresh.counts.get("shared") == 8 * 25


def test_main_reset_and_revoke(tmp_path):
    state = tmp_path / "state"
    reg = TaskRegistry(state / "tasks.json")
    reg.register("job", 4, "s")
    store = BudgetStore(state)
    assert store.check_and_consume("job", 4) == (True, 1)
    assert main(["--state-dir", str(state), "--reset", "job"]) == 0
    assert BudgetStore(state).counts.get("job", 0) == 0
    assert main(["--state-dir", str(state), "--revoke", "job"]) == 0
    assert TaskRegistry(state / "tasks.json").get("job") is None
    assert main(["--state-dir", str(state), "--revoke", "job"]) == 0  # idempotent
