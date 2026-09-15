# templates — env-template policies (source of truth)

One JSON per environment pattern. Full schema + guidance:
`docs/template-reference.md`. Interview: `bash environment/scripts/env-setup`.

| Alias | Port | Budget | For |
| --- | --- | --- | --- |
| `default.json` | 8888 | 200 | general coding |
| `strict.json` | 8889 | 50 | untrusted / one-shot (tool gate, narrow net) |
| `offline.json` | 8890 | 50 | local-only (empty allowlist, no DNS) |
| `web.json` | 8891 | 1000 | research-heavy |
| `node.json` | 8892 | 300 | frontend (pnpm/bun caches) |
| `python.json` | 8893 | 300 | Python (pip/uv caches) |

Rules: edit via `env-setup` (validates on save), never hand-edit generated
profile/netfilter/allowlist files, re-render with `--render-all`. Ports are
unique per template because one proxy instance = one port + one allowlist +
one budget default.
