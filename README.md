# gulyas

Parallel-agent sandbox for a single repo: one Herdr worktree workspace per task,
each agent jailed with Firejail, web access limited to an agreed domain
allowlist with a per-task request budget.

Herdr itself is a terminal multiplexer, not a sandbox — it gives each task an
isolated checkout, and the tooling in this repo confines what the agent inside
that checkout can touch (filesystem) and reach (network).

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
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --port 8888 &
# from your repo's main checkout:
herdr worktree create --branch feat/my-task --no-focus
bash environment/scripts/herdr-agent-firejail --worktree ~/.herdr/worktrees/<repo>/feat-my-task \
  --kind claude --budget-id feat-my-task --budget-max 200 -- claude
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
    proxy/config/allowlist.txt         # agreed domains
    proxy/tests/test_proxy.py          # allowlist + budget unit tests
    firejail/herdr-agent.profile       # sandbox profile (installed to ~/.config/firejail/)
    firejail/herdr-netfilter.net       # DROP direct egress, allow lo:8888 + DNS
    scripts/herdr-agent-firejail       # launch wrapper (sets HERDR_AGENT + proxy env)
    scripts/setup.sh                   # delegates to ../../init.sh (back-compat)
    docker/Dockerfile.proxy            # containerized proxy alternative
    docker/compose.yml                 # host-network runner for that image
```

## Security notes

- Prompt text (`AGENTS.md`: "stay in repo") is advisory only; enforcement here
  is Firejail + proxy + agent `deny` rules. See the plan doc §7 for limits
  (user namespaces required, HTTP/HTTPS only, no protection against malicious
  code already committed in the repo — review `postinstall`/`Makefile`).
- Audit trail: `firejail --audit` + `~/.local/state/herdr-web-proxy/access.log`
  + `herdr pane read`.

## License

This project is released into the public domain under [The Unlicense](https://unlicense.org) — see [`LICENSE`](LICENSE).
