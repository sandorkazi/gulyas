# scripts — wrapper, interview, provisioning, shared library

## `herdr-agent-firejail` — launch an agent inside the jail

```bash
herdr-agent-firejail [--template ALIAS] --worktree PATH [--kind K]
  [--budget-id ID] [--budget-max N] [--proxy-port P] -- <agent> [args…]
herdr-agent-firejail --list-templates
herdr-agent-firejail --template <alias> --show-template
```

Resolves the template via stdlib Python (no `jq`), URL-encodes `BUDGET_ID`
into `http://<id>@127.0.0.1:<port>` so stock tools send per-task identity as
`Proxy-Authorization` (works for `CONNECT`), picks installed
(`~/.config/…`) over repo files, then `exec firejail --profile … --blacklist
~/.herdr/worktrees --noblacklist/--whitelist $WORKTREE --netfilter … --env …`.

## `env-setup` — template interview (create/edit/clone/render)

```bash
bash environment/scripts/env-setup            # interactive
python3 environment/scripts/env-setup --render-all   # non-interactive, all templates
GULYAS_TEMPLATES_DIR=<dir> …                # override template dir (tests use this)
```

Flow: list templates → `new | edit | clone | render` → alias → strictness
preset (`minimal/standard/strict/custom`) → per-flag drill-down (incl.
`read_only_dirs`, `noblacklist`) → proxy port, domains, budget → agent kind →
validate + save JSON + render profile/netfilter/allowlist.

## `provision-worktree` — per-worktree agent layer (idempotent)

```bash
bash environment/scripts/provision-worktree --worktree <WT>
```

Copies `agent/claude-settings.json` → `<WT>/.claude/settings.json`,
appends `agent/AGENTS.md.snippet` to `<WT>/AGENTS.md` once. Wire into your
Herdr worktree-setup plugin.

## `lib_env_template.py` — shared schema + render logic (stdlib-only)

`validate_template` (alias/port/budget/strictness + `noblacklist`,
`private_etc`, `agent.*`, `direct_egress`, `allow_dns`;
`is_valid_domain_pattern` rejects `*`, schemes, ports, paths),
`artifact_paths(repo_root=)`, `write_artifacts(repo_root=)`. Used by
`env-setup`, `init.sh` and the tests.

## `setup.sh` — back-compat shim delegating to `../../init.sh`
