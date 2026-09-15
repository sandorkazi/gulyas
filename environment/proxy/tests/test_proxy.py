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

from herdr_web_proxy import BudgetStore, ProxyServer, ProxyHandler, host_allowed  # noqa: E402
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


def _proxy(tmp_path, allowlist, budget_max=10, default_budget="default"):
    state = tmp_path / "pstate"
    state.mkdir(exist_ok=True)
    budgets = BudgetStore(state)
    srv = ProxyServer(("127.0.0.1", 0), ProxyHandler,
                      allowlist=allowlist, budgets=budgets,
                      default_budget_id=default_budget,
                      budget_max=budget_max,
                      access_log=state / "access.log")
    _start(srv)
    return srv


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
