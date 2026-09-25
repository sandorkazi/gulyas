# Music Catalog — Spec

Date: 2026-09-25. Location: `music-catalog/` (this repo).

## Vision

Music catalog engine as a **local-first CLI tool**: monitor playlists,
keep a clean artist list, keep 0–5 tracks per artist (target 2), fill gaps
with popular-but-dissimilar candidates, browse genre/track similarity space.

## State (reachable, git-tracked)

- All catalog state lives in-repo under `music-catalog/state/` so every
  herdr worktree, agent, and the user see the same thing after push/pull:
  `catalog.json` (artists/tracks/aliases), `review.json` (unknown queue +
  pending merges), `snapshots/` (timestamped monitor diffs).
- JSON is source of truth, schemas versioned; SQLite (if added later) is
  a derived cache, never the authority.
- Secrets (Spotify OAuth, YouTube API key) are **never** in state: env /
  local config only (`*.local.json` git-ignored).
- Concurrent agents: one writer at a time per file; monitors append
  snapshots, merges go through `artist review` to avoid conflicts.

## Functional requirements

1. **Engine as a tool** — CLI (`catalog <cmd>`), stdlib-first Python,
   file-backed store (JSON/SQLite), scriptable + human-usable.
2. **Monitor a YouTube playlist** — poll playlist items, extract
   artist/track, record added-at, diff new vs known.
3. **Monitor a Spotify playlist** — same contract as (2); needs auth
   (OAuth token from env/config, never committed).
4. **Artist registry (merged, deduped, user-controlled)** — merge YT +
   Spotify sources; normalize names; dedupe with alias map; `unknown`
   goes to review queue, never silently dropped/merged. User
   approves merges via CLI (`artist merge/list/review`).
5. **0–5 tracks per artist, preferably 2** — per-artist cap 5, soft
   target 2; store rank/source/popularity; pin/lock tracks.
6. **Import from arbitrary export JSON** — `catalog import file.json`
   with tolerant parser + field mapping + dry-run + report
   (added/skipped/duplicates/unknowns).
7. **Gap detection** — `catalog gaps` lists artists with < 2 tracks
   (0-track and 1-track split), sortable/filterable.
8. **Candidate lookup** — for a gap artist: fetch tracks, rank by
   popularity, pick **2 popular but least similar** (audio-feature /
   genre-tag distance); present candidates for user select/reject.
9. **Similarity-space visualization** — 2D projection of genres+artists
   in one view, tracks in another; hover/label, click to inspect;
   "most similar / least similar to X" query from the same data.
   Static HTML export (no server required).

## Non-functional

- Local-first, offline-capable except monitor/lookup steps.
- No secrets in repo; `offline` gulyas template for pure-local work.
- Small modules, tested (pytest), JSON schemas versioned.

## Out of scope (v1) / long-term goal (v2)

- v1: read-only monitors. No auto-downloading audio, no remote writes.
- v2 goal: **publish the curated playlist (target 2 tracks/artist) to
  both YouTube and Spotify** — `catalog publish --to youtube|spotify|both
  --dry-run` first, explicit confirm before any remote write, full
  undo/preview log. Design the `Source` interface in v1 with a
  write-path stub so v2 adds publishers without reshaping core.
