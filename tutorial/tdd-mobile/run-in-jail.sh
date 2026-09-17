#!/usr/bin/env bash
# run-in-jail.sh — run the tutorial TDD loop inside the repo guardrails.
#
# What it wires up (all repo tooling, no hand-rolled sandbox):
#   1. offline allowlist proxy on 127.0.0.1:8890 (empty allowlist: every fetch
#      403s; direct egress is DROPped by the Firejail netfilter).
#   2. herdr-agent-firejail --template offline: filesystem jail (sibling
#      worktrees blacklisted, only this checkout whitelisted), proxy env with
#      per-task budget userinfo, HERDR_AGENT/HERDR_WORKTREE/BUDGET_* env.
#   3. the loop itself (agentic_loop.py) as the jailed command.
#
# Usage:
#   bash tutorial/tdd-mobile/run-in-jail.sh [--worktree PATH] [--budget-id ID] [--budget-max N] [--proxy-only] [--no-jail]
#   Defaults: --worktree <repo root> --budget-id tdd-mobile --budget-max 50
#   --proxy-only  start the offline proxy and exit (for Docker/manual flows).
#   --no-jail     run the loop with proxy env but without firejail (CI without user namespaces).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKTREE="$ROOT"; BUDGET_ID="tdd-mobile"; BUDGET_MAX="50"
PROXY_ONLY=0; NO_JAIL=0
while [[ $# -gt 0 ]]; do case "$1" in
  --worktree) WORKTREE="$2"; shift 2;;
  --budget-id) BUDGET_ID="$2"; shift 2;;
  --budget-max) BUDGET_MAX="$2"; shift 2;;
  --proxy-only) PROXY_ONLY=1; shift;;
  --no-jail) NO_JAIL=1; shift;;
  -h|--help) sed -n '1,14p' "$0"; exit 0;;
  *) echo "bad arg $1" >&2; exit 2;;
esac; done

PY="$ROOT/.venv/bin/python"; [[ -x "$PY" ]] || PY="python3"
ALLOWLIST="$ROOT/environment/proxy/config/allowlist-offline.txt"
STATE_DIR="${STATE_DIR:-$HOME/.local/state/herdr-web-proxy}"
mkdir -p "$STATE_DIR"

# 1) Offline proxy (one instance owns port 8890 + empty allowlist + budget).
# Health signal for the offline template is 403 (empty allowlist denies `/`):
# any HTTP response at all means the proxy is up.
proxy_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8890/ 2>/dev/null || true)"
if [[ "$proxy_code" =~ ^[0-9]+$ ]]; then
  echo "proxy: already up on 127.0.0.1:8890 (HTTP $proxy_code; 403 is healthy for offline)"
else
  echo "proxy: starting offline instance on 127.0.0.1:8890 …"
  # shellcheck disable=SC2094
  "$PY" "$ROOT/environment/proxy/src/herdr_web_proxy.py" \
    --port 8890 --allowlist "$ALLOWLIST" --budget-max "$BUDGET_MAX" \
    >>"$STATE_DIR/proxy-8890.log" 2>&1 &
  sleep 1
  proxy_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8890/ 2>/dev/null || true)"
  [[ "$proxy_code" =~ ^[0-9]+$ ]] \
    && echo "proxy: up (HTTP $proxy_code; log: $STATE_DIR/proxy-8890.log)" \
    || { echo "proxy: failed to start — see $STATE_DIR/proxy-8890.log" >&2; exit 1; }
fi
[[ "$PROXY_ONLY" == 1 ]] && exit 0

# Preflight: the vanilla templates whitelist /usr + /bin (read_only_toolchain).
# On container/overlayfs hosts Firejail rejects that ("invalid whitelist path
# /usr" — see usage-guide.md troubleshooting). Fail loudly with guidance
# instead of a cryptic jail error; --no-jail keeps proxy+budget+audit.
if [[ "$NO_JAIL" == 0 ]]; then
  if ! firejail --noprofile --whitelist=/usr -- /bin/true >/dev/null 2>&1; then
    cat >&2 <<EOF
run-in-jail: full jail unavailable here (firejail cannot whitelist /usr on
this container/overlayfs host — repo-known limitation, not a tutorial bug).
Options:
  --no-jail   run the loop with proxy env + per-task budget + audit (no Firejail)
  real host   re-run this script where Firejail whitelists /usr (verified path)
EOF
    exit 3
  fi
fi

# 2) + 3) Jailed loop. The wrapper registers (id,max,secret) in tasks.json,
# exports the proxy URL with budget userinfo, and jails the command.
if [[ "$NO_JAIL" == 1 ]]; then
  echo "loop: running WITHOUT firejail (proxy env only) …"
  bash "$ROOT/environment/scripts/herdr-agent-firejail" --template offline \
    --worktree "$WORKTREE" --budget-id "$BUDGET_ID" --budget-max "$BUDGET_MAX" \
    --state-dir "$STATE_DIR" --register-only
  SECRET=$(STATE_F=$STATE_DIR BID=$BUDGET_ID python3 -c 'import json,os;print(json.load(open(os.environ["STATE_F"]+"/tasks.json"))[os.environ["BID"]]["secret"])' 2>/dev/null || true)
  http_proxy="http://${BUDGET_ID}:${SECRET}@127.0.0.1:8890"
  https_proxy="$http_proxy"
  export http_proxy https_proxy
  export HTTP_PROXY="$http_proxy" HTTPS_PROXY="$http_proxy" NO_PROXY=localhost,127.0.0.1
  export BUDGET_ID BUDGET_MAX HERDR_AGENT=claude HERDR_WORKTREE="$WORKTREE"
  exec "$PY" "$ROOT/tutorial/tdd-mobile/agentic_loop.py" --budget-max "$BUDGET_MAX"
else
  echo "loop: launching jailed (template=offline worktree=$WORKTREE budget=$BUDGET_ID/$BUDGET_MAX) …"
  exec bash "$ROOT/environment/scripts/herdr-agent-firejail" --template offline \
    --worktree "$WORKTREE" --budget-id "$BUDGET_ID" --budget-max "$BUDGET_MAX" \
    --state-dir "$STATE_DIR" -- "$PY" "$ROOT/tutorial/tdd-mobile/agentic_loop.py" \
    --budget-max "$BUDGET_MAX"
fi
