# firejail — generated filesystem + network jails

One profile + one netfilter per env template. **Never hand-edit** — they are
rendered from `environment/templates/<alias>.json` via
`bash environment/scripts/env-setup` (or `--render-all`, also run by `init.sh`)
and symlinked into `~/.config/firejail/`.

## Files

| Alias | Profile | Netfilter (port) |
| --- | --- | --- |
| `default` | `herdr-agent.profile` | `herdr-netfilter.net` (:8888) |
| `strict` | `herdr-agent-strict.profile` | `herdr-netfilter-strict.net` (:8889) |
| `offline` | `herdr-agent-offline.profile` | `herdr-netfilter-offline.net` (:8890, no DNS) |
| `web` | `herdr-agent-web.profile` | `herdr-netfilter-web.net` (:8891) |
| `node` | `herdr-agent-node.profile` | `herdr-netfilter-node.net` (:8892) |
| `python` | `herdr-agent-python.profile` | `herdr-netfilter-python.net` (:8893) |

## What the profile enforces

`noroot · nonewprivs · seccomp (+block-secondary) · caps.drop all ·
private-dev · private-tmp · private-etc (minimal) · dbus none · x11 none ·
disable-mnt · restrict-namespaces ·
read-only toolchain (/usr, /bin) · whitelist caches · blacklist secrets
(~/.ssh, ~/.gnupg, ~/.aws, …)`. `strict` adds a `private-bin` tool gate.

`memory-deny-write-execute` is intentionally **off** in all vanilla templates:
Bun/Node JITs (opencode TUI, `bun:ffi`) need writable+executable memory and
fail without it. Re-enable per template via `filesystem.memory_deny_write_execute`
only for agents verified to run without a JIT.

Syntax notes: `noprofile` is a CLI flag and must never appear in a profile;
X11 is disabled with `x11 none` (accepted since Firejail 0.9.80).

## What the netfilter enforces

`iptables-restore` format: default-DROP on INPUT/FORWARD/OUTPUT, ACCEPT
loopback + established, ACCEPT `127.0.0.1:<template-port>`, ACCEPT DNS `:53`
(except `offline`). Direct web egress DROPs — the proxy is the only way out.

## Wrapper-added flags (per instance, see `../scripts/herdr-agent-firejail`)

`--blacklist=$HOME/.herdr/worktrees` (hides **sibling** worktrees) +
`--noblacklist=$WORKTREE` + `--whitelist=$WORKTREE` +
`--netfilter=<template>.net` + `--env` (`HERDR_AGENT`, `HERDR_WORKTREE`,
`BUDGET_ID/MAX`, proxy URLs, `NO_PROXY`).
