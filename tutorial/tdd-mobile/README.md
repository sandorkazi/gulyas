# Tutorial: TDD mobile Tasks app — `tutorial/tdd-mobile/`

Companion to [`docs/tutorial-tdd-mobile-loop.md`](../../docs/tutorial-tdd-mobile-loop.md).
A deterministic, stdlib-only **agentic loop** that builds a mobile-app core
(`TodoStore`) test-first — `RED → GREEN → REFACTOR` per task — **inside the
repo guardrails**: Firejail filesystem jail, netfilter DROP of direct egress,
offline allowlist proxy (`:8890`, empty allowlist), per-task budget, JSONL
audit. No API key, no network, no SDK.

## Layout

```text
tutorial/tdd-mobile/
  README.md            # you are here
  app_spec.md          # mobile app spec (3 screens, portable core + thin UI)
  tasks.json           # ordered backlog (the loop consumes it in order)
  agentic_loop.py      # the agent: plans, writes tests, patches src, runs pytest
  run-in-jail.sh       # repo-tooling entrypoint: proxy → firejail wrapper → loop
  src/todo_store.py    # implementation (--reset restores the v0 stub)
  src/__init__.py
  tests/test_todo_store.py  # generated incrementally by the loop
  .state/              # loop budget.json + audit.jsonl (gitignored)
```

`TodoStore` is framework-free so `pytest` runs in the `offline` jail.
The spec maps 1:1 to a native UI — see `app_spec.md` §4.

## Run (sandboxed — the point of the tutorial)

```bash
./init.sh                                          # once: .venv + render + links
bash tutorial/tdd-mobile/run-in-jail.sh            # proxy :8890 → preflight → jail → loop (5 tasks)
tail -n 20 tutorial/tdd-mobile/.state/audit.jsonl  # loop audit (budget/agent/worktree per line)
tail -n 20 ~/.local/state/herdr-web-proxy/access.log  # proxy audit (real allowlist decisions)
```

`run-in-jail.sh` preflights the jail and exits `3` with guidance on
container/overlayfs hosts (Firejail cannot whitelist `/usr` there — a
repo-known host limitation). There, use `--no-jail` (proxy + budget + audit,
no Firejail) or a real host for the full jail.

Useful variants:

```bash
python3 tutorial/tdd-mobile/agentic_loop.py --show-plan   # backlog, no writes
python3 tutorial/tdd-mobile/agentic_loop.py --check-guardrails  # prove jail holds (also valid inside jail)
bash tutorial/tdd-mobile/run-in-jail.sh --no-jail         # proxy env without firejail (CI w/o user namespaces)
bash tutorial/tdd-mobile/run-in-jail.sh --proxy-only      # just start :8890, run the loop yourself
```

Reset to the starting line (stub src + header-only tests):

```bash
python3 tutorial/tdd-mobile/agentic_loop.py --reset
python3 -m pytest tutorial/tdd-mobile/tests -q    # expect: no tests ran (RED not yet written)
```

## The loop (what `agentic_loop.py` does)

For each entry in `tasks.json`, in order:

1. **RED** — append that task's test snippet to `tests/test_todo_store.py`,
   run `pytest`, expect **fail**.
2. **GREEN** — write the next cumulative `src/todo_store.py` version
   (minimal code to pass all tests so far), run `pytest`, expect **pass**.
3. **REFACTOR** — normalize whitespace, re-run full suite, expect **pass**.

Each `pytest` costs 1 loop-budget unit (`--budget-max`, default from
`$BUDGET_MAX` set by the wrapper; offline template default 50, the loop needs
15). Budget exhausted → exit `2`, mirroring the proxy's `429
budget-exhausted`. Every step logs to `.state/audit.jsonl` with the wrapper's
`BUDGET_ID` / `HERDR_AGENT` / `HERDR_WORKTREE` attached.

## Guardrails this exercises

| Layer | Repo source | What the tutorial proves |
|---|---|---|
| Filesystem jail | `environment/templates/offline.json` → `firejail/herdr-agent-offline.profile` | `--check-guardrails` shows `~/.herdr/worktrees` siblings invisible inside the jail |
| No direct egress | `firejail/herdr-netfilter-offline.net` (DROP, no `:53`) | direct TCP `example.com:80` times out inside the jail |
| Allowlist proxy | `environment/proxy/src/herdr_web_proxy.py` + `proxy/config/allowlist-offline.txt` (empty) | any proxied fetch 403s as `domain-not-allowed`; `access.log` records it |
| Per-task budget | wrapper `--budget-id/--budget-max` → `tasks.json` + `Proxy-Authorization` | `--show-template` prints the registered cap; loop exit `2` mirrors proxy `429` |
| Agent scope | `environment/agent/*` + `provision-worktree` | full worktree flow in the tutorial doc §2 provisions deny rules + `AGENTS.md` scope |

## Swap in a real agent

Scripted patches (`FULL_SOURCES` in `agentic_loop.py`) stand in for an LLM.
Keep the loop shape and replace the GREEN write with one agent call — the
jail, proxy, budget and audit stay identical. Full worktree flow:

```bash
herdr worktree create --branch tutorial/tdd-mobile --no-focus
bash environment/scripts/provision-worktree --worktree ~/.herdr/worktrees/<repo>/tutorial-tdd-mobile
bash environment/scripts/herdr-agent-firejail --template offline \
  --worktree ~/.herdr/worktrees/<repo>/tutorial-tdd-mobile \
  --budget-id tdd-mobile --budget-max 50 -- claude
```

Details: `docs/tutorial-tdd-mobile-loop.md` §5. Docker proxy alternative:
`docs/tutorial-tdd-mobile-loop.md` §6.
