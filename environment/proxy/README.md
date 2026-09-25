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
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --revoke <budget-id>
```

Flags: `--port` (default 8888) · `--allowlist` · `--budget-id` (fallback
identity) · `--budget-max` (default for *unregistered* IDs) · `--state-dir`
(`~/.local/state/herdr-web-proxy`, holds `budget.json` + `tasks.json` +
`access.log`) · `--reset ID` (clear counter) · `--revoke ID` (drop task
registry entry + counter). All have `$PROXY_PORT/$ALLOWLIST/$BUDGET_ID/$BUDGET_MAX/$STATE_DIR` env equivalents.

## Policy

- `config/allowlist[-<alias>].txt` — generated from the template, one
  domain/line; `*.ex.com` matches subdomains only (never the bare domain).
- Budget identity order: `Proxy-Authorization` userinfo (`id:secret`, works
  for GET **and** CONNECT) → `X-Budget-Id` (plain HTTP only) → last-seen ID
  per client IP → `--budget-id` default. The auth header is stripped before
  forwarding.
- Per-task caps: the wrapper registers `(id, max, secret)` in
  `<state-dir>/tasks.json` at launch (reloaded automatically, no restart).
  Registered tasks are capped at their own max and must present the secret —
  wrong/missing secret → `403 budget-auth-failed` without consuming budget.
  Unregistered IDs fall back to the instance `--budget-max` with no auth
  check. So `--budget-max` on the wrapper is enforced, and one task cannot
  bill usage to another's ID by accident.
- Counters update under an exclusive `flock`, so several template proxies
  may share one state dir without losing counts.
- Decisions: allow → forward; off-allowlist → `403 domain-not-allowed`;
  bad secret → `403 budget-auth-failed`; over-budget → `429
  budget-exhausted`; upstream failure → `502`. Every decision is logged
  **before** writing the response (no audit race); log records carry an
  `auth: ok|failed|none` field.
- Any HTTP method to an allowed host on any port is forwarded, up to budget —
  see `docs/security-model.md` residual risk. Tokens stop accidental
  cross-task billing, not same-UID snooping (`/proc` cmdline is visible to
  the same user) — see the security model.

## Tests

```bash
.venv/bin/pytest environment/proxy/tests -q   # 38 passed
```

`test_proxy.py`: allowlist matching + live localhost forward/403/429/reset/
auth-strip/auth-over-header precedence/JSONL + task registry roundtrip,
per-task max enforcement, secret rejection without consumption, hot-reload,
shared-file counters, `--reset`/`--revoke`. `test_env_template.py`: template
schema/render/wrapper/provision coverage (incl. `--register-only`).
