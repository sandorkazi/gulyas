# agent + docker — second layer and alternative runtime

## `agent/` — defense in depth (provisioned per worktree)

- `claude-settings.json` — agent-native `allow` (in-worktree edits, `git/npm/
  python/pytest`, `WebFetch(domain:…)` mirroring the default template),
  `ask: [WebFetch]` fallback, `deny` (outside-worktree reads, `.env`,
  `sudo`, `curl --noproxy`, `wget`, `ssh`). Enforced by the agent CLI, not by
  the jail — the proxy still 403s anything off-allowlist.
- `AGENTS.md.snippet` — advisory scope block ("stay in worktree, use proxy,
  don't escalate, review build scripts"). Prompt text never enforces.
- Applied with `bash environment/scripts/provision-worktree --worktree <WT>`
  (idempotent; safe to re-run).

## `docker/` — containerized proxy alternative

Host `.venv` stays the default (reachable under the loopback firewall rule
with zero port publishing). Use the image for CI / shared runners.

- `Dockerfile.proxy` — carries `proxy/src` + whole `proxy/config/` dir;
  `ALLOWLIST=/app/allowlist.d/allowlist[-<alias>].txt`,
  `PROXY_PORT` must match the template port, `EXPOSE 8888-8893`.
- `compose.yml` — `network_mode: host` (so the Firejail `lo` rule still
  applies), one service per template via overrides:

```bash
docker compose -f environment/docker/compose.yml up --build
TEMPLATE_SUFFIX=-strict PROXY_PORT=8889 BUDGET_MAX=50 \
  docker compose -f environment/docker/compose.yml up --build
```
