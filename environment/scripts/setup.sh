#!/usr/bin/env bash
# setup.sh — back-compat shim. Canonical bootstrap is ../../init.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec bash "$ROOT/init.sh" "$@"
