# gulyas — Template reference

One named jail+proxy policy per environment pattern:
`environment/templates/<alias>.json` (version 1). The checked-in profile,
netfilter and allowlist are **generated** — never hand-edit; re-render with
`bash environment/scripts/env-setup` (or `--render-all`).

## 1. Vanilla matrix

| Alias | Port | Budget | Jail | Allowlist |
| --- | --- | --- | --- | --- |
| `default` | 8888 | 200 | standard | `github.com`, `*.githubusercontent.com`, `registry.npmjs.org`, `*.npmjs.org`, `files.pythonhosted.org`, `*.pypi.org`, `api.anthropic.com` |
| `strict` | 8889 | 50 | strict + `private-bin` tool gate (`bash sh env git python3 node npm ls cat grep sed find`), extra secret blacklists (`.pki .docker .git-credentials`), no broad `~/.cache` | `github.com`, `api.anthropic.com` |
| `offline` | 8890 | 50 | standard | *(empty — every fetch 403)* + **no DNS** (`allow_dns: false`) |
| `web` | 8891 | 1000 | standard | default + `stackoverflow.com`, `*.stackexchange.com`, `docs.python.org`, `developer.mozilla.org`, `crates.io`, `*.crates.io`, `proxy.golang.org`, `pkg.go.dev` |
| `node` | 8892 | 300 | standard + pnpm/bun caches | `github.com`, `*.githubusercontent.com`, `registry.npmjs.org`, `*.npmjs.org`, `api.anthropic.com` |
| `python` | 8893 | 300 | standard + pip/uv caches | `github.com`, `*.githubusercontent.com`, `files.pythonhosted.org`, `*.pypi.org`, `api.anthropic.com` |

Run one proxy per template you use (same port/allowlist/budget as its row).
`default` keeps historic unsuffixed filenames for back-compat.

## 2. Schema

| Key | Meaning → rendered output |
| --- | --- |
| `alias` | `[a-z0-9][a-z0-9-_]{0,63}`; selected via `--template <alias>` |
| `description` | one line, shown by `env-setup` startup list and `--list` |
| `filesystem.strictness` | `minimal` / `standard` (recommended) / `strict` / `custom` (per-flag) |
| `filesystem.noroot/nonewprivs/seccomp/private_tmp/private_dev/disable_mnt/nox11/nodbus/restrict_namespaces/memory_deny_write_execute` | booleans → Firejail profile directives (`nox11: true` renders `x11 none`; `noprofile` is CLI-only and never rendered) |
| `filesystem.private_etc` | `private-etc` file list |
| `filesystem.read_only_toolchain/read_only_dirs` | `read-only` + `whitelist` toolchain paths |
| `filesystem.writable_caches` | `whitelist`ed package-cache dirs (avoids re-downloads per worktree) |
| `filesystem.blacklist/noblacklist` | `blacklist`ed secret paths / exempted worktree dir |
| `filesystem.allowed_tools` | bare basenames → `private-bin` tool gate; empty = unrestricted |
| `network.proxy_port` | 1–65535; baked into netfilter `ACCEPT 127.0.0.1:<port>` + wrapper proxy env |
| `network.allowlist` | domains → `allowlist[-<alias>].txt`; `*.ex.com` matches subdomains only; validated lowercase `example.com` / `*.example.com` (bare `*`, schemes, ports, paths rejected) |
| `network.budget_max_default` | default `BUDGET_MAX` unless `--budget-max` passed (positive int) |
| `network.allow_dns` | whether netfilter keeps the `:53` rules; direct egress always DROPped |
| `network.direct_egress` | reserved; always `false` (any direct egress would contradict the proxy model) |
| `agent.default_kind` | default `HERDR_AGENT` unless `--kind` passed |
| `agent.deny_outside_worktree` | documents that `provision-worktree` agent `deny` rules apply |

## 3. Artifact mapping

| Alias | Profile | Netfilter | Allowlist |
| --- | --- | --- | --- |
| `default` | `firejail/herdr-agent.profile` | `firejail/herdr-netfilter.net` | `proxy/config/allowlist.txt` |
| `<name>` | `firejail/herdr-agent-<name>.profile` | `firejail/herdr-netfilter-<name>.net` | `proxy/config/allowlist-<name>.txt` |

`env-setup` validates on save (alias shape, port range, positive budget, known
strictness) and refuses invalid templates; the same checks run in
`proxy/tests/test_env_template.py`. `GULYAS_TEMPLATES_DIR` overrides the
template directory (used by tests to avoid stray files).

## 4. Authoring guidance

- **Clone, don't widen.** Need an extra domain, cache or tool? `env-setup →
  clone` the closest vanilla template rather than punching holes in the shared one.
- **Python stays jailable** because the wrapper whitelists the whole worktree
  R/W: project-local `.venv`s and editable installs work with zero extra holes;
  only *shared* caches (`~/.cache/pip`, `~/.cache/uv`) are whitelisted.
  System-wide installs (`sudo pip`, conda envs outside the worktree) are
  intentionally unsupported — that is the jail working.
- **Tool gating** (`allowed_tools` → `private-bin`): empty = all host tools
  visible; non-empty = jail's `/bin` restricted to those binaries. An agent
  needing a missing binary fails loudly — add it in a clone.
- **Checklist before shipping a template:** render succeeds, profile parses on
  Firejail ≥ 0.9.80, `pytest` passes, live `N1–N3/B1` verified against real
  domains, `--show-template` output reviewed.
