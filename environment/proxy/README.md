# proxy — stdlib-only allowlist egress proxy

Localhost HTTP forward proxy (`GET http://host/…`) + HTTPS tunnel
(`CONNECT host:443`) with domain allowlist, per-task request budget and
JSONL audit log. No third-party runtime imports; `requirements.txt` is
pytest-only.

## Run

```bash
.venv/bin/python environment/proxy/src/herdr_web_proxy.py \
  --port 8888 --allowlist environment/proxy/config/allowlist.txt --budget-max 200 &
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --reset <budget-id>
```

Flags: `--port` (default 8888) · `--allowlist` · `--budget-id` (fallback
identity) · `--budget-max` · `--state-dir`
(`~/.local/state/herdr-web-proxy`, holds `budget.json` + `access.log`) ·
`--reset ID`. All have `$PROXY_PORT/$ALLOWLIST/$BUDGET_ID/$BUDGET_MAX/$STATE_DIR` env equivalents.

## Policy

- `config/allowlist[-<alias>].txt` — generated from the template, one
  domain/line; `*.ex.com` matches subdomains only (never the bare domain).
- Budget identity order: `Proxy-Authorization` userinfo (works for GET **and**
  CONNECT) → `X-Budget-Id` (plain HTTP only) → last-seen ID per client IP →
  `--budget-id` default. The auth header is stripped before forwarding.
- Decisions: allow → forward; off-allowlist → `403 domain-not-allowed`;
  over-budget → `429 budget-exhausted`; upstream failure → `502`. Every
  decision is logged **before** writing the response (no audit race).
- Any HTTP method to an allowed host on any port is forwarded, up to budget —
  see `docs/security-model.md` residual risk.

## Tests

```bash
.venv/bin/pytest environment/proxy/tests -q   # 27 passed
```

`test_proxy.py`: allowlist matching + live localhost forward/403/429/reset/
auth-strip/auth-over-header precedence/JSONL. `test_env_template.py`: template
schema/render/wrapper/provision coverage.
