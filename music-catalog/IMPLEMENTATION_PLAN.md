# Music Catalog — Implementation plan (gulyas + herdr + OpenCode Zen free)

Companion to `SPEC.md`. Strategy: **one small herdr worktree per phase**,
**offline-first**, **stop/resume at every checkpoint** to survive
OpenCode Zen free rate limits.

## 0. Ground rules (token discipline)

- Model: stay on Zen **free** (`opencode/muse-spark-1.3-contributor-free`
  or other `*-free` IDs from `https://opencode.ai/docs/zen/`). Free =
  rate-limited + may be used for training — never paste secrets.
- One task per session. Keep prompts < ~2k tokens: point at
  `music-catalog/SPEC.md` + the single phase file, not the whole repo.
- Stop-and-continue protocol per task:
  1. `opencode run` with a single acceptance criterion.
  2. On `free usage exceeded / rate limit`: stop, `git commit`, note
     last green test in `music-catalog/PROGRESS.md`, resume later —
     state lives in git, not in context.
  3. Rehydrate with: "read SPEC.md + PROGRESS.md + `git log --oneline -5`,
     continue at next unchecked box".
- Prefer `offline` template for pure-local code; `python` only when
  `pip install` needed; `web` only for API-docs research spikes.

## 1. Work breakdown → herdr worktrees

| # | Branch / worktree | Template | Budget | Scope (acceptance) |
|---|---|---|---|---|
| 0 | `feat/catalog-scaffold` | `offline` :8890, max 50 | 50 | `music-catalog/` pkg skeleton, `catalog --help`, pytest green |
| 1 | `feat/catalog-core` | `offline`, 50 | 50 | artist/track store + alias map + unknown queue; `artist merge/list/review` |
| 2 | `feat/catalog-import` | `offline`, 50 | 50 | `catalog import file.json --dry-run` + report counts |
| 3 | `feat/catalog-monitors` | `python` :8893, 100 | 100 | YT + Spotify pollers behind a `Source` interface, fakes in tests; no secrets |
| 4 | `feat/catalog-gaps` | `offline`, 50 | 50 | `catalog gaps` + candidate scorer (popularity − similarity), 2-pick |
| 5 | `feat/catalog-viz` | `offline`, 50 | 50 | static HTML genre/artist + track maps + similar/dissimilar query |
| 6 | `feat/catalog-polish` | `offline`, 50 | 50 | docs, `PROGRESS.md` complete, `./init.sh --smoke` green |
| 7 (v2, later) | `feat/catalog-publish` | `python` :8893 | 100 | `catalog publish --to youtube\|spotify\|both --dry-run`, confirm + undo log; NOT in v1 |

State is git-tracked (`music-catalog/state/`), so phases share it via
normal merge — each worktree rebases before starting, commits state
schema changes explicitly. Secrets stay out (env-only).

Total ≈ 400 proxy requests if run jailed; each row is independently
resumable. Never combine rows in one session.

Per-task commands (from repo root, once `herdr` is installed):

```bash
./init.sh
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --port 8890 \
  --allowlist environment/proxy/config/allowlist-offline.txt --budget-max 50 &
herdr worktree create --branch feat/catalog-scaffold --no-focus
bash environment/scripts/provision-worktree --worktree ~/.herdr/worktrees/gulyas/feat-catalog-scaffold
bash environment/scripts/herdr-agent-firejail --template offline \
  --worktree ~/.herdr/worktrees/gulyas/feat-catalog-scaffold \
  --budget-id catalog-scaffold --budget-max 50 -- opencode
# inside jail: opencode with model opencode/muse-spark-1.3-contributor-free
# cleanup: herdr worktree remove --workspace <id>; .../herdr_web_proxy.py --revoke catalog-scaffold
```

Repeat per row with its branch/budget-id/template port
(offline 8890 / python 8893). `python` row allowlist must add only
`files.pythonhosted.org`, `*.pypi.org` (already in `python.json`).

## 2. Data sketch (phase 1 contract, keep stable; lives at `music-catalog/state/catalog.json`)

```json
{"artists": [{"id": "a1", "name": "…", "aliases": [], "status": "ok|unknown|merged"}],
 "tracks": [{"id": "t1", "artist_id": "a1", "title": "…", "source": "youtube|spotify|import",
             "popularity": 0, "features": {}, "pinned": false}]}
```

Rules: cap 5/artist, target 2; unknowns → `state/review.json`; merges explicit.
Phase 3 builds the `Source` interface read-only but with a write-path
stub (`publish(dry_run=True)`) so v2 phase 7 plugs in YT + Spotify publishers.

## 3. Resume log

Track in `music-catalog/PROGRESS.md`: `- [ ]` per phase row above +
last green commit. Update it before every stop.
