# gulyas — Security model

What the jail enforces, what is merely advisory, and what residual risk
you accept every time you grant an agent network access.

Companion: `architecture.html` §2–§4 (diagrams), `usage-guide.md` §8
(troubleshooting), `herdr-firejail-sandbox-plan.md` §7 (ops notes).

## 1. Enforcement layers (strongest first)

| # | Layer | Enforced by | Breaks if you… |
| --- | --- | --- | --- |
| 1 | Filesystem jail | Firejail profile + wrapper flags (`--blacklist` siblings, `--whitelist` own worktree, `noroot`, `nonewprivs`, `seccomp`, `caps.drop all`, no D-Bus/X11) | launch the agent without the wrapper; hand-edit generated profiles; run on a kernel without user namespaces |
| 2 | Network allowlist + budget | Netfilter DROP-direct-egress + localhost proxy (`allowlist.txt` + per-task registry `(max, secret)` in `tasks.json` + `count[BUDGET_ID] < max` + JSONL audit) | point the agent at the wrong proxy port; widen the allowlist; set an oversized budget; let the agent use `--noproxy` (mitigated: netfilter DROPs it anyway, agent `deny` rules reinforce) |
| 3 | Agent-native rules | `provision-worktree` → `.claude/settings.json` (`deny` outside-worktree reads, `sudo`, `curl --noproxy`, `wget`, `ssh`; `WebFetch` allowlist with `ask` fallback) | skip provisioning; use an agent CLI that ignores these files |
| 4 | Advisory scope | `AGENTS.md` snippet ("stay in repo, use proxy, don't escalate") | prompt-injection or a careless model — **never rely on this** |

Prompt text is advisory only. The audit trail is
`firejail --audit` + `~/.local/state/herdr-web-proxy/access.log` + `herdr pane read`.

## 2. Threat model

- **Adversary:** untrusted or buggy agent CLI (`claude`, `codex`, `opencode`, …)
  running as your UID. It may try to read host credentials, sibling checkouts,
  or exfiltrate data over the network.
- **Protected:** host secrets (`~/.ssh`, `~/.gnupg`, `~/.aws`, …), other
  checkouts, direct (unproxied) egress, off-allowlist domains, over-budget volume.
- **Explicitly NOT protected:** malicious code already committed in the repo
  (`postinstall`, `Makefile`, build scripts) — review before running;
  harm to *other* machines via allowed domains (see §3);
  data loss *inside* the task's own worktree (non-goal);
  full-VM guarantees (use Vercel Sandbox / Docker `sbx` if you need that).

## 3. Residual risk — read before choosing a template

1. **Reads are not the only risk.** The proxy allowlist is host-only: any HTTP
   method (`GET` *and* `POST`/`PUT`/`DELETE`) to an allowed domain is forwarded,
   on any port. An agent can submit data outward and speak non-HTTP protocols
   to allowed hosts, up to its request budget.
2. **Fetching code is executing code.** `pip install` / `npm install` run
   remote build scripts holding the agent's full network allowance. A malicious
   or typosquatted package acts with everything the template permits — from
   inside your jail, against the outside world.
3. **Budgets cap volume, not intent.** A few hundred requests are plenty for
   exfiltration or abuse of a third party. The audit log records what happened
   but does not prevent it. Per-task caps + secrets stop *accidental*
   cross-task billing (wrong ID, shared helper scripts) — not a same-UID
   snooper reading another task's proxy URL from `/proc` cmdline or its own
   jail env. Against a deliberately malicious same-UID agent, treat the
   budget as accounting, not a security boundary.
4. **DNS stays open** (`:53`) for proxy resolution unless the template sets
   `allow_dns: false` (`offline`); direct TCP/UDP egress stays DROPped.
   Raw TCP/UDP/ICMP is intentionally unsupported (HTTP/HTTPS proxy only).

Consequence: **do not treat the stock templates as security postures.** They
are starting points. Tailor the policy to the actual task with
`bash environment/scripts/env-setup` — narrowest workable allowlist, smallest
workable budget, `offline` for purely local work, `strict` for untrusted runs —
and review the audit log per task. The right question is never "is the template
safe?" but "what can this task's agent reach, and what could that reach do
elsewhere?"

## 4. Operator duties (per task)

- [ ] Pick the narrowest template that still lets the task succeed
      (`offline` → `strict` → `default`/`node`/`python` → `web`, in that order).
- [ ] Set the smallest workable `--budget-max`; use distinct `--budget-id` per task.
- [ ] Verify live `N1–N3/B1` against real domains per template before use.
- [ ] Verify `F1–F3/P1` + `H1` on a real host (user namespaces + Herdr binary;
      container overlayfs cannot whitelist `/usr` — that error is environmental).
- [ ] Review `postinstall` / `Makefile` / build scripts already in the repo.
- [ ] After the run: read `access.log`, `herdr pane read`, reset the budget,
      remove the worktree.

## 5. Acceptance tests (must pass before use)

`F1` outside-read blocked · `F2` sibling-worktree invisible ·
`F3` worktree R/W works · `N1` allowed domain 200 · `N2` denied domain 403 ·
`N3` direct bypass DROP · `B1` budget 429 + `budget-exhausted` in log ·
`P1` two jails in parallel, distinct `BUDGET_ID`s · `H1` Herdr classifies pane.
Full definitions in `herdr-firejail-sandbox-plan.md` §6.
