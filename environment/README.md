# Environment — where Python runs and how to use it

Decision: **Python proxy runs on the host in `gulyas/.venv` by default.**
Docker holds an equivalent image only as an alternative (`environment/docker/`).

Why: Firejail's netfilter allows `127.0.0.1:8888` on loopback. A host `.venv`
proxy is reachable under that rule with zero port publishing. A container needs
`network_mode: host` to land on the same loopback, which is fine but adds a
moving part. Keep host default; container for CI / shared runners.

## Layout

```text
gulyas/
  .venv/                          # host python env (gitignored), created by setup.sh
  environment/
    README.md                     # this file
    proxy/src/herdr_web_proxy.py  # stdlib-only, no pip deps needed at runtime
    proxy/config/allowlist.txt    # agreed domains (generated from templates/default.json)
    proxy/tests/test_proxy.py     # pytest, runs in .venv
    proxy/tests/test_env_template.py  # template schema/render/wrapper tests
    templates/<alias>.json        # env templates: default, strict, offline, web, node, python
    firejail/herdr-agent.profile  # installed to ~/.config/firejail/ (generated from template)
    firejail/herdr-netfilter.net  # iptables-restore, referenced by wrapper (generated)
    scripts/herdr-agent-firejail  # firejail wrapper: --template ALIAS, sets HERDR_AGENT + proxy env
    scripts/env-setup             # interactive interview: create/edit env templates
    scripts/lib_env_template.py   # shared template schema + render logic (stdlib-only)
    scripts/setup.sh              # creates .venv, installs test deps, links configs
    docker/Dockerfile.proxy       # alternative: same proxy in a container
    docker/compose.yml            # alternative runner (network_mode: host)
```

## Quickstart (host, recommended)

```bash
bash environment/scripts/setup.sh
bash environment/scripts/env-setup   # pick or define an env template (alias)
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --port 8888 &
.venv/bin/pytest environment/proxy/tests -q
bash environment/scripts/herdr-agent-firejail --template default --worktree ~/.herdr/worktrees/myrepo/feat-x \
  --budget-id feat-x --budget-max 200 -- claude
```

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

| Alias | Use when | Jail | Network |
| --- | --- | --- | --- |
| `default` | general coding, the agreed policy | standard | agreed list, budget 200 |
| `strict` | untrusted / one-shot runs | strict + `private-bin` tool gate (bash, git, python3, node, …), extra secret blacklists, no broad `~/.cache` | github + model API only, budget 50 |
| `offline` | pure local refactors, zero exfiltration surface | standard | empty allowlist (every fetch 403) + no DNS; budget 50 as backstop |
| `web` | research-heavy tasks | standard | default list + Stack Overflow, Python/MDN docs, crates.io, Go proxy; budget 1000 |
| `node` | frontend work | standard + pnpm/bun caches | npm registries + github + model API, budget 300 |
| `python` | Python work | standard + pip/uv caches | PyPI + github + model API, budget 300 |

All vanilla templates share proxy port 8888, so one proxy serves every
concurrent agent regardless of template.

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
| `network.proxy_port` | loopback port baked into the netfilter rule + wrapper proxy env (1..65535) |
| `network.allowlist` | domains → `allowlist[-<alias>].txt`; `*.ex.com` matches subdomains only |
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
docker compose -f environment/docker/compose.yml up --build
# then run wrapper as usual (proxy still on 127.0.0.1:8888 via host network)
```

## Notes

* `herdr` binary is assumed per plan but not on PATH on this machine yet —
  `setup.sh` warns instead of failing so firejail/proxy work can proceed.
* Runtime proxy has no third-party imports; `requirements.txt` is only pytest.
* State + audit log: `~/.local/state/herdr-web-proxy/{budget.json,access.log}`.
