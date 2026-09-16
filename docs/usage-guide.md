# gulyas — Usage guide

Daily workflows for task owners: bootstrap once, then per-task
proxy → worktree → provision → jail → audit → cleanup.

See [`architecture.html`](architecture.html) for the pictures,
[`template-reference.md`](template-reference.md) for policy fields,
[`security-model.md`](security-model.md) for what the jail does *not* cover.

## 0. Requirements

| Tool | Needed for | Check |
| --- | --- | --- |
| Python 3.10+ | allowlist proxy (host default) | `python3 --version` |
| Firejail ≥ 0.9.80 | agent filesystem/net jail | `firejail --version` |
| Herdr | worktree workspaces, agent panes | `herdr --version` |
| Docker ≥ 24 | only the containerized proxy alternative | `docker --version` |

`init.sh` warns (does not fail) on missing optional tools so proxy/firejail
work can proceed without Herdr installed.

## 1. Bootstrap (once per checkout)

```bash
git clone <this-repo> gulyas && cd gulyas
./init.sh                 # .venv + test deps + render templates + symlink configs
.venv/bin/pytest environment/proxy/tests -q   # expect 34 passed
./init.sh --check         # tool status only, changes nothing
./init.sh --smoke         # setup + run proxy unit tests
```

What `init.sh` links (repo stays source of truth, never copy):

- `environment/firejail/*.profile`, `*.net` → `~/.config/firejail/`
- `environment/proxy/config/allowlist*.txt` → `~/.config/herdr-web-proxy/`
- State dir created: `~/.local/state/herdr-web-proxy/` (`budget.json`, `access.log`)

## 2. Start the proxy (one instance per template you use)

Each template has its own loopback port, allowlist and budget default —
a single proxy cannot enforce several templates at once.

```bash
# default template (port 8888, budget 200):
.venv/bin/python environment/proxy/src/herdr_web_proxy.py \
  --port 8888 --allowlist environment/proxy/config/allowlist.txt --budget-max 200 &

# strict template (port 8889, budget 50):
.venv/bin/python environment/proxy/src/herdr_web_proxy.py \
  --port 8889 --allowlist environment/proxy/config/allowlist-strict.txt --budget-max 50 &

# web / node / python / offline follow the same pattern (8891 / 8892 / 8893 / 8890).
# See environment/README.md "Shipped vanilla templates" table for the full matrix.
```

Proxy CLI reference (`herdr_web_proxy.py`):

| Flag | Default | Meaning |
| --- | --- | --- |
| `--port` | `8888` (`$PROXY_PORT`) | listen on `127.0.0.1:<port>` only |
| `--allowlist` | `proxy/config/allowlist.txt` (`$ALLOWLIST`) | one domain/line, `*.ex.com` = subdomains only |
| `--budget-max` | `200` (`$BUDGET_MAX`) | max requests for *unregistered* IDs on this instance |
| `--budget-id` | `default` (`$BUDGET_ID`) | fallback identity when no userinfo/header seen |
| `--state-dir` | `~/.local/state/herdr-web-proxy` (`$STATE_DIR`) | `budget.json` + `tasks.json` + `access.log` live here |
| `--reset <id>` | — | reset that task's counter and exit |
| `--revoke <id>` | — | drop that task's registry entry + counter and exit |

Docker alternative (same proxy, containerized; host `.venv` stays default):

```bash
docker compose -f environment/docker/compose.yml up --build   # default :8888
TEMPLATE_SUFFIX=-strict PROXY_PORT=8889 BUDGET_MAX=50 \
  docker compose -f environment/docker/compose.yml up --build # strict :8889
```

Note: the container keeps its own `proxy-state` volume, so task registrations
written by the wrapper to the host state dir do **not** reach it — registered
tasks would fall back to the instance `--budget-max` with no auth check.
Bind-mount the host state dir over `/state` if you need per-task caps with
the docker proxy.

## 3. Create + provision the worktree (per task)

```bash
# from your repo's main checkout:
herdr worktree create --branch feat/my-task --no-focus
herdr worktree list --cwd ~/code/myrepo
bash environment/scripts/provision-worktree --worktree ~/.herdr/worktrees/<repo>/feat-my-task
```

`provision-worktree` is idempotent: copies
`environment/agent/claude-settings.json` → `<WT>/.claude/settings.json`
and appends `AGENTS.md.snippet` to `<WT>/AGENTS.md` once (skips if present).
Wire it into your Herdr worktree-setup plugin so no worktree ships without it.

## 4. Launch the agent in the jail (per task)

```bash
bash environment/scripts/herdr-agent-firejail \
  --template default \
  --worktree ~/.herdr/worktrees/<repo>/feat-my-task \
  --budget-id feat-my-task --budget-max 200 -- claude
```

Wrapper flags:

| Flag | Meaning |
| --- | --- |
| `--template ALIAS` | `environment/templates/<alias>.json` (default: `default`) |
| `--worktree PATH` | **required.** the task's own checkout; siblings stay blacklisted |
| `--kind K` | `HERDR_AGENT` value Herdr uses to classify the pane (default from template) |
| `--budget-id ID` | URL-encoded into proxy userinfo → `Proxy-Authorization` → per-task accounting |
| `--budget-max N` | registered as this task's cap in `<state-dir>/tasks.json`; **enforced** by the proxy |
| `--budget-secret S` | task token for proxy auth (default: fresh random per launch; explicit = reproducible) |
| `--state-dir D` | proxy state dir holding `tasks.json` (default: `$STATE_DIR` or `~/.local/state/herdr-web-proxy`) |
| `--register-only` | register the task budget and exit — no jail launch (docker flows, tests) |
| `--proxy-port P` | override template port (must match the running proxy) |
| `--list-templates` | list aliases and exit |
| `--show-template` | print resolved profile/netfilter/allowlist + proxy command and exit (secret redacted) |

Inspect before launching:

```bash
bash environment/scripts/herdr-agent-firejail --list-templates
bash environment/scripts/herdr-agent-firejail --template strict --show-template
```

Environment the agent sees inside the jail:

```text
HERDR_AGENT=<kind>  HERDR_WORKTREE=<wt>  BUDGET_ID=<id>  BUDGET_MAX=<n>
http_proxy=http://<id>@127.0.0.1:<port>  https_proxy=…  HTTP_PROXY=…  HTTPS_PROXY=…
NO_PROXY=localhost,127.0.0.1
```

Any agent CLI works after `--`: `claude`, `codex`, `opencode`, `bash`, …

## 5. Operate: budgets, logs, parallel tasks

```bash
# tail decisions live (JSONL: ts host decision budget_id count method code auth):
tail -f ~/.local/state/herdr-web-proxy/access.log

# budget exhausted (429)? reset the task counter, or revoke the task entirely:
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --reset feat-my-task
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --revoke feat-my-task

# launching registers (id, max, secret) in <state-dir>/tasks.json automatically —
# no proxy restart needed. Register without launching (docker flows, tests):
bash environment/scripts/herdr-agent-firejail --template default \
  --budget-id feat-my-task --budget-max 200 --state-dir ~/.local/state/herdr-web-proxy \
  --register-only

# parallel tasks: distinct BUDGET_IDs share one template proxy safely:
bash environment/scripts/herdr-agent-firejail --template default \
  --worktree ~/.herdr/worktrees/<repo>/feat-a --budget-id feat-a -- claude &
bash environment/scripts/herdr-agent-firejail --template default \
  --worktree ~/.herdr/worktrees/<repo>/feat-b --budget-id feat-b -- claude &
# strongest isolation: one proxy per task with --budget-id fixed instead.
```

Expected proxy responses:

| Probe | Expect |
| --- | --- |
| `curl -x http://127.0.0.1:8888 https://github.com -I` | `200` (N1) |
| `curl -x http://127.0.0.1:8888 https://evil.example -I` | `403 domain-not-allowed` (N2) |
| `curl --noproxy '*' https://github.com -I --max-time 5` | timeout / DROP (N3) |
| 4th fetch with `BUDGET_MAX=3` | `429 budget-exhausted` (B1) |

## 6. Create / edit a policy (template author flow)

```bash
bash environment/scripts/env-setup
# 1) lists existing templates (alias, description, domains, budget)
# 2) new | edit | clone | render
#    new/clone asks alias first, then strictness preset → per-flag drill-down
#    (noroot, seccomp, private-tmp/dev, secret blacklists, caches, allowed_tools)
#    → proxy port, allowed domains, budget default → agent kind
# 3) saves JSON + renders profile + netfilter + allowlist
```

Non-interactive re-render of everything (also run by `init.sh`):

```bash
python3 environment/scripts/env-setup --render-all
```

Rules: JSON is source of truth; generated files carry a `do not hand-edit`
header. Validation rejects bare `*`, schemes, ports, paths, whitespace and
uppercase. Override dir in tests via `GULYAS_TEMPLATES_DIR`.

## 7. Cleanup (per task)

```bash
herdr worktree remove --workspace <child-id>   # git worktree remove, keeps branch
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --revoke feat-my-task
```

`--revoke` drops the task's registry entry *and* counter; `--reset` clears
only the counter (keeps the cap + secret).

## 8. Troubleshooting

| Symptom | Likely cause → fix |
| --- | --- |
| `firejail: line … is invalid` | stale hand-edited profile → re-render (`env-setup --render-all`), never hand-edit generated files |
| agent sees sibling worktrees | wrapper bypassed / old profile → always launch via `herdr-agent-firejail` (adds `--blacklist ~/.herdr/worktrees`) |
| every fetch `403` | wrong proxy port/allowlist for the template, or `offline` template → `--show-template` to compare, start matching proxy |
| unexpected `429` | budget spent → check `access.log` counts, `--reset <id>` or raise `--budget-max` |
| `502 upstream-error` | DNS/TCP failure (offline template has no DNS by design) → check allowlist + `allow_dns` |
| `herdr agent list` misclassifies pane | `HERDR_AGENT` not set → launch via wrapper (sets `--env=HERDR_AGENT=…`) |
| `invalid whitelist path /usr` in container | container-overlayfs-only artifact, not a repo bug; verify on a real host |
| `pip install` runs remote scripts with full allowance | by design — review packages, narrow allowlist/budget, prefer `offline` for local work |
| tests write `herdr-agent-web-strict.*` strays | old code path; current tests isolate under `$GULYAS_TEMPLATES_DIR` — update checkout |
