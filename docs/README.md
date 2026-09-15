# gulyas — Documentation index

> Sandboxed parallel coding agents on a single repo: one task per isolated
> checkout, each agent jailed to its own files and an agreed web allowlist
> with a per-task request budget.

## Plan behind this doc set

The repo has two audiences: **task owners** (who launch agents) and
**policy authors** (who define what agents may reach). The docs are split
along that line so neither has to wade through the other's detail:

| Doc | Audience | Answers |
| --- | --- | --- |
| [`architecture.html`](architecture.html) | everyone, first | What are the pieces, how do they fit, what flows where? (6 high-level diagrams, offline-safe) |
| [`usage-guide.md`](usage-guide.md) | task owners | How do I bootstrap, start proxies, provision worktrees, launch agents, reset budgets, debug? |
| [`template-reference.md`](template-reference.md) | policy authors | What does each `environment/templates/<alias>.json` field render to? Which vanilla template do I clone? |
| [`security-model.md`](security-model.md) | policy authors, reviewers | What is enforced vs advisory? What residual risk remains? What must I tailor per task? |
| [`herdr-firejail-sandbox-plan.md`](herdr-firejail-sandbox-plan.md) | implementers | Full build-ready design, acceptance tests `F1–H1`, build checklist |
| `../environment/README.md` | operators | Host-`.venv` vs Docker decision, per-template proxy commands |
| `../README.md` | newcomers | 5-minute quickstart + repo layout + limitations summary |

## Structure of the tool (map)

```text
gulyas/
  init.sh                               # canonical bootstrap
  docs/                                 # you are here
    architecture.html                   # ← high-level diagrams (this set's visual entry)
    usage-guide.md                      # ← daily workflows, troubleshooting
    template-reference.md               # ← schema + vanilla matrix + render rules
    security-model.md                   # ← threat model + residual risk + ops duties
    herdr-firejail-sandbox-plan.md      # original design + acceptance tests
  environment/
    templates/<alias>.json              # SOURCE OF TRUTH (policy)
    firejail/herdr-agent[-<alias>].profile   # generated jail
    firejail/herdr-netfilter[-<alias>].net   # generated DROP-direct-egress rules
    proxy/src/herdr_web_proxy.py        # stdlib-only allowlist + budget proxy
    proxy/config/allowlist[-<alias>].txt     # generated domain lists
    scripts/herdr-agent-firejail        # launch wrapper (--template, budget userinfo, sibling blacklist)
    scripts/env-setup                   # interview to create/edit templates
    scripts/provision-worktree          # per-worktree agent deny rules + AGENTS.md scope
    scripts/lib_env_template.py         # shared schema/render logic
    agent/claude-settings.json + AGENTS.md.snippet  # defense-in-depth layer
    docker/Dockerfile.proxy + compose.yml           # containerized proxy alternative
```

## Reading order

1. New here? Root `README.md` quickstart → `architecture.html` §1–§3.
2. Running a task? `usage-guide.md` start to finish.
3. Defining policy? `template-reference.md` + `security-model.md`, then
   `bash environment/scripts/env-setup`.
4. Auditing? `security-model.md` § audit trail + `usage-guide.md` § debugging.
