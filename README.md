# gulyas

Sandboxed parallel coding agents on a single repo: run one task per isolated
checkout, each agent jailed to its own files and an agreed web allowlist with
a per-task request budget.

Two halves, each replaceable:

1. **[Herdr](https://herdr.dev) for orchestration** — a terminal multiplexer
   that keeps one agent per pane with `working/blocked/done` status. Here it
   provides the isolation unit: one git worktree = one grouped Herdr workspace
   (`herdr worktree create --branch <name>`), so parallel tasks don't share
   files or branches. Herdr is not a sandbox — confinement below is separate.
2. **Everything else in this repo for confinement** —
   [Firejail](https://firejail.wordpress.com/) filesystem + network jail,
   a stdlib-only localhost egress proxy (domain allowlist + request budget +
   audit log), agent permission configs (`deny` outside the worktree,
   `WebFetch` allowlist), and Docker as an alternative proxy runtime.

## How it works

- **Isolation unit:** one git worktree = one Herdr grouped workspace
  (`herdr worktree create --branch <name>`).
- **Filesystem jail:** `environment/firejail/herdr-agent.profile` + per-worktree
  `--whitelist`, `noroot`, `nonewprivs`, `seccomp`, no D-Bus/X11, `~/.ssh` and
  sibling checkouts blocked.
- **Network allowlist + budget:** Firejail netfilter DROPs direct egress; the
  only way out is the localhost proxy
  (`environment/proxy/src/herdr_web_proxy.py`, stdlib-only) which checks
  `environment/proxy/config/allowlist.txt` and a per-task counter, then logs
  every decision as JSONL. Denies are `403 domain-not-allowed` /
  `429 budget-exhausted`.
- **Env templates:** one named jail+proxy policy per environment pattern
  (`environment/templates/<alias>.json`, created via the `env-setup`
  interview). The profile, netfilter, and allowlist above are generated from
  the template — never hand-edited — and the wrapper selects one with
  `--template <alias>`.
- **Python placement:** the proxy runs on the host in `./.venv` by default
  (reachable under the loopback firewall rule with no port publishing).
  `environment/docker/` holds an equivalent image as an alternative.

Full design: [`docs/herdr-firejail-sandbox-plan.md`](docs/herdr-firejail-sandbox-plan.md).
Environment details: [`environment/README.md`](environment/README.md).

## Requirements

| Tool | Needed for | Check |
| --- | --- | --- |
| Python 3.10+ | allowlist proxy (host default) | `python3 --version` |
| Firejail ≥ 0.9.80 | agent filesystem/net jail | `firejail --version` |
| Herdr | worktree workspaces, agent panes | `herdr --version` |
| Docker ≥ 24 | only the containerized proxy alternative | `docker --version` |

`init.sh` warns (does not fail) on missing optional tools so proxy/firejail
work can proceed without Herdr installed.

## Quickstart

```bash
git clone <this-repo> gulyas && cd gulyas
./init.sh                 # creates .venv, installs test deps, links firejail configs
.venv/bin/pytest environment/proxy/tests -q
bash environment/scripts/env-setup   # interview: pick/edit an env template (alias) for firejail+proxy policy
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --port 8888 &
# from your repo's main checkout:
herdr worktree create --branch feat/my-task --no-focus
bash environment/scripts/herdr-agent-firejail --template default --worktree ~/.herdr/worktrees/<repo>/feat-my-task \
  --budget-id feat-my-task --budget-max 200 -- claude
```

Reset a task budget: `.venv/bin/python environment/proxy/src/herdr_web_proxy.py --reset <budget-id>`

## Repo layout

```text
gulyas/
  init.sh                              # canonical bootstrap (this README's entrypoint)
  docs/herdr-firejail-sandbox-plan.md  # goals, threat model, acceptance tests
  environment/
    README.md                          # host-.venv vs docker decision + usage
    proxy/src/herdr_web_proxy.py       # stdlib-only egress proxy
    proxy/config/allowlist.txt         # agreed domains (generated from template)
    proxy/tests/test_proxy.py          # allowlist + budget unit tests
    proxy/tests/test_env_template.py # template schema/render/wrapper tests
    templates/<alias>.json           # env templates: default, strict, offline, web, node, python
    firejail/herdr-agent.profile     # sandbox profile (generated from template, installed to ~/.config/firejail/)
    firejail/herdr-netfilter.net     # DROP direct egress, allow lo:8888 + DNS (generated)
    scripts/herdr-agent-firejail     # launch wrapper (--template ALIAS, sets HERDR_AGENT + proxy env)
    scripts/env-setup                # interactive interview: create/edit env templates (alias + firejail drill-down)
    scripts/lib_env_template.py      # shared template schema/render logic (stdlib-only)
    scripts/setup.sh                   # delegates to ../../init.sh (back-compat)
    docker/Dockerfile.proxy            # containerized proxy alternative
    docker/compose.yml                 # host-network runner for that image
```

## Limitations & residual risk

The jail contains the agent's *local* blast radius. It does **not** make a
network-capable agent harmless — especially to *other* machines and networks:

- **Reads are not the only risk.** The proxy allowlist is host-only: any HTTP
  method (`GET` *and* `POST`/`PUT`/`DELETE`) to an allowed domain is
  forwarded, on any port. An agent can submit data outward and speak non-HTTP
  protocols to allowed hosts, up to its request budget.
- **Fetching code is executing code.** `pip install` / `npm install` run
  remote build scripts holding the agent's full network allowance. A
  malicious or typosquatted package acts with everything the template
  permits — from inside your jail, against the outside world.
- **Budgets cap volume, not intent.** A few hundred requests are plenty for
  exfiltration or abuse of a third party. The audit log
  (`~/.local/state/herdr-web-proxy/access.log`) records what happened but
  does not prevent it.

Consequence: **do not treat the stock templates as security postures.** They
are starting points. Tailor the policy to the risks of the actual usage with
`bash environment/scripts/env-setup` — narrowest workable allowlist,
smallest workable budget, `offline` for purely local work, `strict` for
untrusted runs — and review the audit log per task. The right question is
never "is the template safe?" but "what can this task's agent reach, and
what could that reach do elsewhere?"

## Security notes

- Prompt text (`AGENTS.md`: "stay in repo") is advisory only; enforcement here
  is Firejail + proxy + agent `deny` rules. See the plan doc §7 for limits
  (user namespaces required, HTTP/HTTPS only, no protection against malicious
  code already committed in the repo — review `postinstall`/`Makefile`).
- Audit trail: `firejail --audit` + `~/.local/state/herdr-web-proxy/access.log`
  + `herdr pane read`.

## License

This project is released into the public domain under [The Unlicense](https://unlicense.org) — see [`LICENSE`](LICENSE).
