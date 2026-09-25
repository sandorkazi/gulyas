# Environment — where Python runs and how to use it

Decision: **Python proxy runs on the host in `gulyas/.venv` by default.**
Docker holds an equivalent image only as an alternative (`environment/docker/`).

Why: Firejail's netfilter allows `127.0.0.1:8888` on loopback. A host `.venv`
proxy is reachable under that rule with zero port publishing. A container needs
`network_mode: host` to land on the same loopback, which is fine but adds a
moving part. Keep host default; container for CI / shared runners.

Hands-on path through all of the below: [`../docs/tutorial-tdd-mobile-loop.md`](../docs/tutorial-tdd-mobile-loop.md)
(jailed agentic TDD loop building a mobile-app core on the `offline` template).

## Layout

```text
gulyas/
  .venv/                          # host python env (gitignored), created by init.sh (via scripts/setup.sh shim)
  environment/
    README.md                     # this file
    proxy/src/herdr_web_proxy.py  # stdlib-only, no pip deps needed at runtime
    proxy/config/allowlist[-<alias>].txt  # agreed domains (generated from templates/<alias>.json)
    proxy/tests/test_proxy.py     # pytest, runs in .venv (allowlist + budget + live proxy)
    proxy/tests/test_env_template.py  # template schema/render/wrapper tests
    templates/<alias>.json        # env templates: default (:8888), strict (:8889), offline (:8890), web (:8891), node (:8892), python (:8893)
    firejail/herdr-agent[-<alias>].profile  # installed to ~/.config/firejail/ (generated from template)
    firejail/herdr-netfilter[-<alias>].net  # iptables-restore, referenced by wrapper (generated)
    scripts/herdr-agent-firejail  # firejail wrapper: --template ALIAS, sets HERDR_AGENT + proxy env + sibling blacklist
    scripts/env-setup             # interactive interview: create/edit env templates
    scripts/provision-worktree    # copy agent deny rules + AGENTS.md scope into a new worktree
    scripts/lib_env_template.py   # shared template schema + render logic (stdlib-only)
    scripts/setup.sh              # back-compat shim delegating to ../../init.sh
    agent/claude-settings.json    # agent-native deny rules (provisioned per worktree)
    agent/AGENTS.md.snippet       # advisory scope block (provisioned per worktree)
    docker/Dockerfile.proxy       # alternative: same proxy in a container (per-template)
    docker/compose.yml            # alternative runner (network_mode: host, one service per template)
```

## Quickstart (host, recommended)

```bash
bash environment/scripts/setup.sh   # shim -> ../../init.sh (canonical bootstrap)
bash environment/scripts/env-setup   # pick or define an env template (alias)
# one proxy per template you use (ports/budgets from the template table below):
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --port 8888 --allowlist environment/proxy/config/allowlist.txt --budget-max 200 &
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --port 8889 --allowlist environment/proxy/config/allowlist-strict.txt --budget-max 50 &
.venv/bin/pytest environment/proxy/tests -q
bash environment/scripts/provision-worktree --worktree ~/.herdr/worktrees/myrepo/feat-x
bash environment/scripts/herdr-agent-firejail --template default --worktree ~/.herdr/worktrees/myrepo/feat-x \
  --budget-id feat-x --budget-max 200 -- claude
```

Budget identity: the wrapper encodes `--budget-id` plus a per-launch secret
in the proxy URL userinfo, so curl/git/pip/npm send it as
`Proxy-Authorization: Basic ...` on every request (including `CONNECT`). The
proxy decodes it first, then `X-Budget-Id`, then last-seen-per-IP, then its
`--budget-id` default — so tasks sharing one template proxy are still
accounted separately. Launch also registers `(id, max, secret)` in
`<state-dir>/tasks.json` (reloaded without restart): the task is capped at
its own `--budget-max` and a wrong/missing secret is denied as `403
budget-auth-failed` without consuming budget. Unregistered IDs fall back to
the instance `--budget-max` with no auth check.

Project CLI: `bin/gulyas init <folder>` scaffolds `gulyas.yaml` (commented
defaults) + `GOAL.md` per task folder; `bin/gulyas run <folder> -- <agent>`
provisions and jails the agent (it execs the wrapper call below with the
config's template/worktree/budget). Full CLI reference: `usage-guide.md`
§1b.

## Env templates (new environment patterns)

An env template is one named jail+proxy policy: `environment/templates/<alias>.json`.
The checked-in Firejail profile, netfilter file, and proxy allowlist are
**generated** from it — never hand-edit them, re-render via `env-setup`.

```bash
bash environment/scripts/env-setup
# 1) starts by listing existing templates (alias, description, domains, budget)
# 2) choose new | edit | clone | render
#    - new/clone asks for an alias first (used as --template <alias>)
#    - then drills down: strictness preset (minimal/standard/strict/custom) ->
#      each Firejail flag (noroot, seccomp, private-tmp/dev, blacklisted secrets,
#      writable caches) -> proxy port, allowed domains, budget default -> agent kind
# 3) saves the JSON + renders firejail/herdr-agent[-<alias>].profile,
#    firejail/herdr-netfilter[-<alias>].net, proxy/config/allowlist[-<alias>].txt
```

Use a template for agent runs:

```bash
bash environment/scripts/herdr-agent-firejail --list-templates
bash environment/scripts/herdr-agent-firejail --template web-strict --show-template
bash environment/scripts/herdr-agent-firejail --template web-strict --worktree <WT> \
  --budget-id <id> -- <agent>
# --budget-max / --kind / --proxy-port override the template defaults when given.
```

### Shipped vanilla templates

| Alias | Port | Use when | Jail | Network |
| --- | --- | --- | --- | --- |
| `default` | 8888 | general coding, the agreed policy | standard | agreed list, budget 200 |
| `strict` | 8889 | untrusted / one-shot runs | strict + `private-bin` tool gate (bash, git, python3, node, …), extra secret blacklists, no broad `~/.cache` | github + model API only, budget 50 |
| `offline` | 8890 | pure local refactors, zero exfiltration surface | standard | empty allowlist (every fetch 403) + no DNS; budget 50 as backstop |
| `web` | 8891 | research-heavy tasks | standard | default list + Stack Overflow, Python/MDN docs, crates.io, Go proxy; budget 1000 |
| `node` | 8892 | frontend work | standard + pnpm/bun caches | npm registries + github + model API, budget 300 |
| `python` | 8893 | Python work | standard + pip/uv caches | PyPI + github + model API, budget 300 |

Each template has its own loopback port, so run one proxy instance per
template you use (same port/allowlist/budget as the row above). A single
proxy cannot enforce several templates at once — the allowlist and budget
default are per proxy instance.

Why Python stays jailable: the wrapper whitelists the whole worktree
read-write at runtime, so project-local `.venv`s, `pip install`, and editable
installs work with zero extra holes — only the *shared* caches
(`~/.cache/pip`, `~/.cache/uv`) are whitelisted. System-wide installs
(`sudo pip`, conda envs outside the worktree) are intentionally unsupported:
that is the jail working, not a gap. If a project needs more, clone the
template via `env-setup` rather than punching holes in the vanilla one.

Tool gating (`filesystem.allowed_tools`, Firejail `private-bin`): restricts
the jail's `/bin` to the named binaries. Empty = all host tools visible
(`default`, `offline`, `web`, `node`, `python`); `strict` gates to a dozen.
An agent needing a missing binary fails loudly — clone the template and add
the tool rather than widening the shared one.

### Template schema & generated files

`environment/templates/<alias>.json` (`version: 1`):

| Key | Meaning → rendered output |
| --- | --- |
| `alias` | `[a-z0-9][a-z0-9-_]{0,63}`; selected via `--template <alias>` |
| `description` | one line, shown by `env-setup` startup list and `--list` |
| `filesystem.strictness` | preset: `minimal` / `standard` (recommended) / `strict` / `custom` (per-flag) |
| `filesystem.noroot/nonewprivs/seccomp/private_tmp/private_dev/disable_mnt/nox11/nodbus/restrict_namespaces/memory_deny_write_execute` | booleans → Firejail profile directives |
| `filesystem.private_etc` | `private-etc` file list |
| `filesystem.read_only_toolchain/read_only_dirs` | `read-only` + `whitelist` toolchain paths |
| `filesystem.writable_caches` | `whitelist`ed package-cache dirs (no re-downloads per worktree) |
| `filesystem.blacklist/noblacklist` | `blacklist`ed secret paths / exempted worktree dir |
| `filesystem.allowed_tools` | bare basenames → `private-bin` tool gate; empty = unrestricted |
| `network.proxy_port` | loopback port baked into the netfilter rule + wrapper proxy env (1..65535; vanilla aliases use 8888..8893, one proxy per template) |
| `network.allowlist` | domains → `allowlist[-<alias>].txt`; `*.ex.com` matches subdomains only; validated to `example.com` / `*.example.com` lowercase (bare `*`, schemes, ports, paths rejected) |
| `network.budget_max_default` | default `BUDGET_MAX` unless `--budget-max` is passed (positive int) |
| `network.allow_dns` | whether the netfilter keeps the `:53` rules; direct egress is always dropped |
| `agent.default_kind` | default `HERDR_AGENT` unless `--kind` is passed |

Artifact mapping (`default` keeps the historic unsuffixed names for back-compat):

| Alias | Profile | Netfilter | Allowlist |
| --- | --- | --- | --- |
| `default` | `firejail/herdr-agent.profile` | `firejail/herdr-netfilter.net` | `proxy/config/allowlist.txt` |
| `<name>` | `firejail/herdr-agent-<name>.profile` | `firejail/herdr-netfilter-<name>.net` | `proxy/config/allowlist-<name>.txt` |

Rules: the JSON is the source of truth — generated files carry a
`do not hand-edit` header. `env-setup` validates on save (alias shape, port
range, positive budget, known strictness) and refuses invalid templates; the
same checks run in `test_env_template.py`. `GULYAS_TEMPLATES_DIR` overrides the
template directory (used by tests).

## Docker alternative

```bash
# default template proxy:
docker compose -f environment/docker/compose.yml up --build
# strict template proxy (port 8889, budget 50):
TEMPLATE_SUFFIX=-strict PROXY_PORT=8889 BUDGET_MAX=50 docker compose -f environment/docker/compose.yml up --build
# then run wrapper as usual (proxy still on 127.0.0.1:<port> via host network)
```

The image carries the whole `proxy/config/` dir; pick the allowlist with
`ALLOWLIST=/app/allowlist.d/allowlist[-<alias>].txt` (compose does this from
`TEMPLATE_SUFFIX`). Run one container per template you use.

## Agent scope (defense in depth)

Firejail + proxy enforce; agent configs add a second layer (advisory to the
model, enforced by the agent CLI):

- `environment/agent/claude-settings.json` — deny outside-worktree reads,
  `sudo`, `curl --noproxy`, `wget`, `ssh`; `WebFetch(domain:…)` allowlist
  (mirrors the default template) with `ask` fallback for other domains.
- `environment/agent/AGENTS.md.snippet` — scope block for `AGENTS.md`.
- `bash environment/scripts/provision-worktree --worktree <WT>` copies both
  into a new worktree (wire it into your Herdr worktree-setup plugin).

### Running opencode in the jail

opencode's TUI and `run` default to a shared background service discovered
via `~/.local/state/opencode/service.json` + `~/.config/opencode/`. Those
paths are hidden by the jail's `whitelist` semantics by design (and reaching
the outside, unjailed server would defeat the jail — the server does the
file/tool work), so bare `opencode` fails with `Timed out waiting for the
background service to start`. Always launch it with `--standalone` (private
in-jail server) **from inside the worktree** (the wrapper jails the
worktree but does not `cd`; opencode uses the caller's cwd as project dir):

```bash
cd ~/.herdr/worktrees/myrepo/feat-x
bash environment/scripts/herdr-agent-firejail --template offline \
  --worktree ~/.herdr/worktrees/myrepo/feat-x \
  --budget-id feat-x --budget-max 50 -- opencode --standalone
```

Do not whitelist opencode's state dirs to "fix" service discovery: that
would expose `~/.local/share/opencode/auth.json` API keys to the jailed
agent and let it reach the unjailed server.

## Notes

* `herdr` binary is assumed per plan but not on PATH on this machine yet —
  `setup.sh` warns instead of failing so firejail/proxy work can proceed.
* Runtime proxy has no third-party imports; `requirements.txt` is only pytest.
* State + audit log: `~/.local/state/herdr-web-proxy/{budget.json,access.log}`.
