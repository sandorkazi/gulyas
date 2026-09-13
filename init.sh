#!/usr/bin/env bash
# init.sh — canonical bootstrap for the gulyas repo.
# Idempotent: safe to re-run. Warns (does not fail) on missing optional tools.
#
# Usage:
#   ./init.sh              # full setup: .venv, test deps, firejail config links
#   ./init.sh --check      # print tool status only, change nothing
#   ./init.sh --smoke      # setup + run proxy unit tests
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="setup"
for arg in "$@"; do
  case "$arg" in
    --check) MODE="check" ;;
    --smoke) MODE="smoke" ;;
    -h|--help)
      echo "Usage: ./init.sh [--check|--smoke]"
      exit 0
      ;;
    *) echo "Unknown arg: $arg (see --help)" >&2; exit 2 ;;
  esac
done

have() { command -v "$1" >/dev/null 2>&1; }
warned=0

echo "== gulyas init @ $ROOT (mode=$MODE)"

# --- tool checks (herdr/docker optional per README) ---
if have python3; then
  echo "OK: python3 ($(python3 --version 2>&1))"
else
  echo "FAIL: python3 is required but not on PATH" >&2; exit 1
fi
if have firejail; then
  echo "OK: firejail ($(firejail --version 2>&1 | head -1))"
else
  echo "WARN: firejail not found — sandbox wrapper will not run here"; warned=1
fi
if have herdr; then
  echo "OK: herdr ($(herdr --version 2>&1 | head -1))"
else
  echo "WARN: herdr not on PATH — worktree steps must run where herdr is installed"; warned=1
fi
if have docker; then
  echo "OK: docker ($(docker --version 2>&1))"
else
  echo "WARN: docker not found — containerized proxy alternative unavailable"; warned=1
fi

[[ "$MODE" == "check" ]] && exit "$warned"

# --- host python env (default proxy runtime) ---
if [[ ! -d "$ROOT/.venv" ]]; then
  echo "-- creating .venv"
  python3 -m venv "$ROOT/.venv"
else
  echo "-- .venv exists, reusing"
fi
echo "-- installing test deps"
"$ROOT/.venv/bin/pip" install -q -r "$ROOT/environment/proxy/requirements.txt"
echo "OK: $("$ROOT/.venv/bin/python" --version 2>&1) @ $ROOT/.venv"

# --- install user-level configs via symlink (never copy: repo stays source of truth) ---
mkdir -p "$HOME/.config/firejail" "$HOME/.config/herdr-web-proxy" "$HOME/.local/state/herdr-web-proxy"
ln -sf "$ROOT/environment/firejail/herdr-agent.profile" "$HOME/.config/firejail/herdr-agent.profile"
ln -sf "$ROOT/environment/firejail/herdr-netfilter.net" "$HOME/.config/firejail/herdr-netfilter.net"
ln -sf "$ROOT/environment/proxy/config/allowlist.txt" "$HOME/.config/herdr-web-proxy/allowlist.txt"
echo "OK: linked firejail profile + netfilter + allowlist"

if [[ "$MODE" == "smoke" ]]; then
  echo "-- running proxy unit tests"
  "$ROOT/.venv/bin/pytest" "$ROOT/environment/proxy/tests" -q
fi

cat <<EOF
== done (warnings: $warned)
next:
  1) .venv/bin/python environment/proxy/src/herdr_web_proxy.py --port 8888 &
  2) herdr worktree create --branch feat/my-task --no-focus   # where herdr lives
  3) bash environment/scripts/herdr-agent-firejail --worktree <WT> --kind claude --budget-id <id> --budget-max 200 -- claude
EOF
