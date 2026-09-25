# Tutorial: a simple agentic TDD loop for a mobile app — inside the guardrails

Build a small mobile todo app ("Gulyas Tasks") test-first with a scripted
agentic loop that runs **jailed**: Firejail filesystem + netfilter,
offline allowlist proxy, per-task budget, audit log. No API key, no network,
no Android/iOS SDK — `pytest` is the whole toolchain, which is exactly why it
fits the `offline` template.

> Companion code: `tutorial/tdd-mobile/` (`README.md` = quick ref, this doc =
> the walkthrough). Repo workflows: `usage-guide.md`; policy fields:
> `template-reference.md`; what the jail does *not* cover: `security-model.md`.

## 0. What you will run

A deterministic loop (`tutorial/tdd-mobile/agentic_loop.py`, stdlib-only)
that consumes `tasks.json` in order. Per task: **RED** (append one failing
test) → **GREEN** (cumulative `src/todo_store.py` patch) → **REFACTOR**
(rerun suite). 5 tasks × 3 `pytest` runs = 15 budget units. The scripted
patches stand in for an LLM; §5 shows where the model call slots in without
changing the jail, proxy, budget or audit.

The app core (`TodoStore`: add / toggle / filter / remove / clear-done /
snapshot) is framework-free and maps 1:1 to native UI — see
`tutorial/tdd-mobile/app_spec.md` §4 (`store.list(f)` → Compose
`filter{}` / SwiftUI `List` filter; `ValueError` → inline form error;
`to_dict/from_dict` → `kotlinx.serialization` / `Codable` snapshot).
Build the native shell *after* the core is green: UI bugs are then binding
bugs, never logic bugs.

Guardrail → tutorial mapping:

| Layer | Repo source of truth | Tutorial proof |
|---|---|---|
| Filesystem jail | `environment/templates/offline.json` → `firejail/herdr-agent-offline.profile` | `--check-guardrails`: sibling `~/.herdr/worktrees` invisible in jail |
| No direct egress | `firejail/herdr-netfilter-offline.net` (DROP all, no `:53`) | direct TCP to `example.com:80` times out in jail |
| Allowlist proxy | `proxy/src/herdr_web_proxy.py` + `proxy/config/allowlist-offline.txt` (empty) | any proxied fetch → `403 domain-not-allowed` + `access.log` line |
| Per-task budget | `herdr-agent-firejail --budget-id/--budget-max` → `tasks.json` + `Proxy-Authorization` | `--show-template` shows the cap; loop exit `2` mirrors proxy `429` |
| Agent scope | `environment/agent/*` + `scripts/provision-worktree` | §2 provisions deny rules + `AGENTS.md` scope into the worktree |

Why `offline`, not `python`? The loop needs zero network (stdlib + local
`pytest`), so the narrowest template is the honest one: empty allowlist, no
DNS, budget 50 as backstop. If you later `pip install` something, clone to
the `python` template (PyPI allowlist, port 8893) via `env-setup` instead of
widening `offline`.

## 1. Bootstrap + start the offline proxy (one instance owns `:8890`)

```bash
./init.sh                 # .venv + render templates + link firejail/proxy configs
.venv/bin/pytest environment/proxy/tests -q   # expect 51 passed

# Start the offline proxy (empty allowlist, budget 50). One proxy per template:
.venv/bin/python environment/proxy/src/herdr_web_proxy.py \
  --port 8890 --allowlist environment/proxy/config/allowlist-offline.txt \
  --budget-max 50 >>~/.local/state/herdr-web-proxy/proxy-8890.log 2>&1 &
# Health signal for the offline template is 403 (empty allowlist denies `/`):
curl -s -o /dev/null -w 'proxy :8890 → HTTP %{http_code} (403 == healthy, offline)\n' \
  --max-time 2 http://127.0.0.1:8890/
```

`run-in-jail.sh` does this for you (reuses the port if already up). Docker
alternative (§6) runs the same proxy image with
`TEMPLATE_SUFFIX=-offline PROXY_PORT=8890 BUDGET_MAX=50`.

Inspect what the jail will enforce before launching:

```bash
bash environment/scripts/herdr-agent-firejail --template offline --show-template
# expect: profile …/herdr-agent-offline.profile, netfilter …-offline.net,
# allowlist …/allowlist-offline.txt, proxy_port=8890, budget_max=50,
# sibling isolation: --blacklist=$HOME/.herdr/worktrees + --whitelist=$WORKTREE
```

## 2. Isolate: worktree + provision (per task)

One task = one checkout = one jail. With Herdr:

```bash
herdr worktree create --branch tutorial/tdd-mobile --no-focus
herdr worktree list --cwd "$PWD"   # find the real path; replaces <repo> below
bash environment/scripts/provision-worktree \
  --worktree ~/.herdr/worktrees/<repo>/tutorial-tdd-mobile
```

`provision-worktree` is idempotent: copies
`environment/agent/claude-settings.json` → `<WT>/.claude/settings.json`
(deny outside-worktree reads, `sudo`, proxy bypass, `ssh`; `WebFetch`
allowlist) and appends the scope block to `<WT>/AGENTS.md` once. Without
Herdr, `git worktree add ../tdd-mobile tutorial/tdd-mobile` gives the same
isolation unit the wrapper jails with `--worktree`.

For this tutorial the default `--worktree` is the current checkout itself —
the wrapper still blacklists *sibling* worktrees, so the guardrail demo in
§3 works either way. (New worktrees only carry *tracked* files: commit new
material first, or run the tutorial from the checkout that contains it.)
If you created the separate worktree above, point the runner at it explicitly —
`run-in-jail.sh` will not guess it for you:

```bash
bash tutorial/tdd-mobile/run-in-jail.sh --worktree ~/.herdr/worktrees/<repo>/tutorial-tdd-mobile
```

## 3. Prove the guardrails hold (inside vs outside the jail)

Outside (raw shell — probes report INFO only; only the jail earns PASS/FAIL):

```bash
python3 tutorial/tdd-mobile/agentic_loop.py --check-guardrails
# guardrails [UNJAILLED (plain shell)] ...
# [guardrail:filesystem] INFO siblings visible=… (no jail here; compare with the jailed run)
# [guardrail:netfilter]  INFO … (host network, not the jail — may connect or fail depending on host)
# [guardrail:proxy]      INFO no http_proxy in env — launch via run-in-jail.sh for the proxied path.
# UNJAILLED — re-run inside the jail (run-in-jail.sh) for PASS/FAIL verdicts.
```

Inside (the wrapper sets `HERDR_*`/`BUDGET_*` + proxy env + jail):

```bash
bash environment/scripts/herdr-agent-firejail --template offline \
  --worktree "$PWD" --budget-id tdd-mobile --budget-max 50 \
  -- python3 tutorial/tdd-mobile/agentic_loop.py --check-guardrails
# guardrails [JAILED] ...
# [guardrail:filesystem] siblings visible=False — PASS
# [guardrail:netfilter]  PASS direct TCP blocked (timeout/DROP)
# [guardrail:proxy]      denied as designed (HTTP Error 403) — PASS
# GUARDRAILS: ALL HOLD
```

> Container hosts: if Firejail cannot whitelist `/usr` there (overlayfs —
> `run-in-jail.sh` preflights this and exits `3` with guidance), the raw
> `--check-guardrails` above fails the same way with `invalid whitelist path
> /usr`. That is a host limitation, documented in `usage-guide.md`
> §8, not a policy bug: the full offline profile minus the two toolchain
> whitelists loads and all three probes PASS. Use `--no-jail` (§4) on such
> hosts — the probes then report INFO + `UNJAILLED` (the honest signal: no
> verdicts outside the jail), not a pass.

Each proxy denial lands in `~/.local/state/herdr-web-proxy/access.log`
(JSONL: `ts host decision budget_id count method code auth`) — the same log
you tail for real agent tasks (`usage-guide.md` §5).

## 4. Run the TDD loop — jailed (the scenario)

One command: proxy (reuse `:8890`) → preflight → jail → loop:

```bash
bash tutorial/tdd-mobile/run-in-jail.sh
```

The script preflights the jail: if Firejail cannot whitelist `/usr` on the
host (container/overlayfs), it exits `3` with guidance instead of a cryptic
jail error — re-run with `--no-jail` (proxy env + per-task budget + audit,
no Firejail) or move to a real host for the full jail. `--no-jail` is also
the CI path without user namespaces.

Expected shape (truncated):

```text
proxy: already up on 127.0.0.1:8890
loop: launching jailed (template=offline worktree=… budget=tdd-mobile/50) …
=== [1/5] t1-add-list: Add tasks and list them ===
RED rc=1 … 1 failed …   # new test fails against the stub — good
GREEN rc=0 … 1 passed … # v1 patch: add + list
REFACTOR rc=0 … 1 passed
… (t2 toggle, t3 filter, t4 remove/clear, t5 snapshot) …
DONE: 5 tasks green, spent=15/50. Audit: .state/audit.jsonl
```

Then confirm from outside the jail:

```bash
python3 -m pytest tutorial/tdd-mobile/tests -q          # expect 5 passed
tail -n 5 tutorial/tdd-mobile/.state/audit.jsonl        # loop audit w/ BUDGET_ID + HERDR_* per line
tail -n 5 ~/.local/state/herdr-web-proxy/access.log     # proxy audit (guardrail probes + any agent fetches)
```

Start over any time: `python3 tutorial/tdd-mobile/agentic_loop.py --reset`
(stub `src` + header-only `tests` + zeroed loop budget), then re-run
`run-in-jail.sh`. Re-running a completed checkout *without* `--reset` stops
with `WARN: RED passed` (exit `1`) by design — the test is already green, so
there is nothing for RED to prove. Resetting the *proxy* counter is separate:
`.venv/bin/python environment/proxy/src/herdr_web_proxy.py --reset tdd-mobile`
(counter only) or `--revoke tdd-mobile` (drop cap + secret too).

CI without user namespaces: `run-in-jail.sh --no-jail` keeps the proxy env +
budget registration but skips Firejail (guardrail §3 probes report INFO +
`UNJAILLED` there — the honest signal, never a pass). The preflight exits `3`
on container/overlayfs hosts for the same reason: `--no-jail` or a real host.

## 5. Where a real model slots in (same loop, same jail)

The loop has three roles; only GREEN needs a model:

- **Planner** — `tasks.json` order (already the backlog; a model could
  re-prioritise, but the file stays the audit source).
- **RED** — test snippet per task (keep scripted: the test *is* the spec, and
  a model-written test that passes against the stub proves nothing).
- **GREEN** — replace the `FULL_SOURCES[step]` write with one agent call
  inside the same jail: the model sees the failing test + current `src`,
  returns a patch, the loop applies it and re-runs `pytest`. Gate on
  `rc == 0` exactly as now; on failure, feed the `pytest` tail back for one
  retry, then stop (a loop that retries forever is a budget incident).
- **REFACTOR** — keep mechanical (format/lint + rerun); let the model propose
  renames only if the suite stays green.

Launch the model the same way — the jail, proxy, budget and audit do not
change, only the command after `--`:

```bash
bash environment/scripts/herdr-agent-firejail --template offline \
  --worktree ~/.herdr/worktrees/<repo>/tutorial-tdd-mobile \
  --budget-id tdd-mobile --budget-max 50 -- claude   # or codex / opencode / bash
```

For a real task folder the same launch goes through the project CLI
(`usage-guide.md` §1b): `init` scaffolds the folder (`gulyas.yaml` with
commented defaults + `GOAL.md`), `run` provisions, registers the budget and
jails the agent — underneath it is exactly the wrapper call above:

```bash
./bin/gulyas init /tmp/tdd-green --template offline --with-agents
# fill in GOAL.md (point it at the failing test + current src), start :8890, then:
./bin/gulyas run /tmp/tdd-green -- claude
```

Budget math: 15 deterministic `pytest` runs cost 15 loop units; a model GREEN
typically adds 1–3 proxy fetches per retry (API + any docs). Cap retries per
task (e.g. 3) so worst case stays ≈ 5 × (3 + 3×3) = 60 — i.e. raise
`--budget-max` to 80 for LLM runs, or keep 50 and let `429` be the backstop.

## 6. Containerised proxy alternative (same policy, Docker runtime)

Host `.venv` is the default because Firejail's loopback rule reaches it with
no port publishing. For CI / shared runners, the same offline policy runs
containerised (image carries the whole `proxy/config/` dir; compose picks the
allowlist from `TEMPLATE_SUFFIX`):

```bash
TEMPLATE_SUFFIX=-offline PROXY_PORT=8890 BUDGET_MAX=50 \
  docker compose -f environment/docker/compose.yml up --build &
curl -s -o /dev/null -w 'proxy :8890 → HTTP %{http_code} (403 == healthy, offline)\n' \
  --max-time 2 http://127.0.0.1:8890/
bash tutorial/tdd-mobile/run-in-jail.sh   # reuses :8890, jail path unchanged
```

Caveat (from `usage-guide.md` §2): the container keeps its own `proxy-state`
volume, so task registrations in the host state dir do not reach it —
registered caps fall back to the instance `--budget-max` with no auth check.
Bind-mount the host state dir over `/state` if the tutorial needs per-task
caps enforced by the container.

## 7. Cleanup (per task)

```bash
python3 tutorial/tdd-mobile/agentic_loop.py --reset   # loop state only
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --revoke tdd-mobile
herdr worktree list --cwd "$PWD"                    # find <child-id> for the next line
herdr worktree remove --workspace <child-id>          # or: git worktree remove ../tdd-mobile
```

`--revoke` drops the task's registry entry *and* counter; `--reset` clears
only the counter (keeps cap + secret).

## 8. Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| `run-in-jail.sh` → `firejail: line … is invalid` | stale hand-edited profile → `python3 environment/scripts/env-setup --render-all`, never hand-edit generated files |
| `--check-guardrails` shows siblings visible / TCP connects inside jail | wrapper bypassed → always launch via `run-in-jail.sh` / `herdr-agent-firejail` (adds sibling blacklist + netfilter) |
| every proxied fetch `403` | correct for `offline` (empty allowlist) → that *is* the demo; for real web work clone the `web` template |
| loop exit `2` / proxy `429 budget-exhausted` | budget spent → `access.log` counts, `--reset tdd-mobile` (counter) or raise `--budget-max` |
| `curl :8890` refuses | proxy not running → `run-in-jail.sh --proxy-only`, check `$STATE_DIR/proxy-8890.log` |
| `invalid whitelist path /usr` in container | container-overlayfs-only artifact, not a repo bug; verify on a real host |
| `run-in-jail.sh` exits `3` / `firejail: invalid whitelist path /usr` | container/overlayfs host (known limitation) → `--no-jail`, or a real host; see §3 box |
| RED passes (`unexpected-pass`) | test does not discriminate (already implemented?) → `--reset` and re-run in order |
| GREEN fails | patch incomplete → read the `pytest` tail above the summary; the loop stops to keep the suite honest |
