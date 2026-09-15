# Herdr + Firejail Sandbox Plan — Parallel Repo Worktrees with FS Jail + Web Allowlist + Request Budget

Status: draft plan / build-ready
Target: multiple agents on the **same repo in parallel**, each confined to its own
Herdr worktree workspace, no escape outside repo, web only to agreed URLs with a request cap.

> Firejail gives filesystem / process isolation. It does **not** do L7 domain
> allowlisting. Domain allowlist + request counting needs a small egress proxy
> (spec'd below). Don't rely on prompt-priming alone.

## 1. Goals / non-goals

Goals:

1. `G1` One task = one git worktree = one Herdr grouped workspace (`herdr worktree create`).
2. `G2` Agent can R/W inside its worktree, read toolchain (`/usr`, `node`, `go`, package caches), R/W its own private `/tmp`.
3. `G3` Agent cannot read/write outside worktree: no `~/.ssh`, `~/.gnupg`, other worktrees, other `$HOME`, no `/etc` writes, no raw sockets / `sudo` / `mount`.
4. `G4` Egress allowlist: only agreed domains (e.g. `github.com`, `registry.npmjs.org`, model API). Everything else denied.
5. `G5` Request budget: max N web requests per agent/task, then deny with clear error. Auditable log.
6. `G6` Parallel-safe: N sandboxes at once, no shared writable state except explicit proxy log dir.

Non-goals: full VM guarantee (use Vercel Sandbox / Docker `sbx` if you need that),
Windows support, GUI/IDE sandboxing, data-loss prevention inside the worktree itself.

Threat model: untrusted / buggy agent CLI (`claude`, `codex`, `opencode`, ...)
running as your UID. Protect host credentials, other checkouts, and network
exfiltration. Does not protect against malicious toolchain binaries already in
repo — still review `postinstall` / `Makefile`.

## 2. Architecture

Host runs Herdr server unsandboxed. Only agent processes are jailed.

```text
Herdr server (host, trusted)
 ├─ workspace parent: ~/code/myrepo (main checkout, no agent or read-only agent)
 ├─ workspace child-1: ~/.herdr/worktrees/myrepo/feat-a  ── firejail instance 1 ── claude
 └─ workspace child-2: ~/.herdr/worktrees/myrepo/feat-b  ── firejail instance 2 ── claude

Each firejail instance:
  whitelist = its worktree path (+ read-only toolchain)
  private-tmp, private-dev, noroot, nonewprivs, seccomp, caps.drop all,
  dbus-user none, dbus-system none
  netfilter = block direct 80/443, allow only 127.0.0.1:<template-port> (8888-8893, one proxy per template)
  env: http_proxy=http://<budget-id>@127.0.0.1:<port> https_proxy=... HERDR_AGENT=<kind> HERDR_WORKTREE=... BUDGET_ID=...

Egress proxy (one per template, localhost only):
  allowlist.txt + per-BUDGET_ID counter -> allow / 429 deny + access.log
```

Herdr detection still works because the wrapper stays host-visible; set
`HERDR_AGENT=<kind>` on the firejail command line (see §5).

## 3. Filesystem policy

Base profile `~/.config/firejail/herdr-agent.profile` (standalone generated profile).

> Source of truth: `environment/templates/<alias>.json`, managed through the
> `environment/scripts/env-setup` interview (alias + strictness preset +
> per-flag drill-down). The checked-in `.profile` / `.net` / `allowlist.txt`
> files are **generated** from the template — never hand-edit them; re-render
> with `env-setup` (choose `render`) or `env-setup --render-all`. `init.sh`
> re-renders and symlinks every template's artifacts. The block below is what
> the `default` template renders.

```ini
noroot
nonewprivs
seccomp
seccomp.block-secondary
caps.drop all
machine-id
private-dev
private-tmp
private-etc passwd,group,hostname,hosts,resolv.conf,ssl,ca-certificates,crypto-policies,pki,nsswitch.conf
dbus-user none
dbus-system none
nogroups
nosound
notv
x11 none
nodvd
disable-mnt
read-only ${HOME}/.config/herdr
blacklist ${HOME}/.ssh
blacklist ${HOME}/.gnupg
blacklist ${HOME}/.aws
blacklist ${HOME}/.config/gh
noblacklist ${HOME}/.herdr/worktrees
whitelist /usr
whitelist /bin
whitelist ${HOME}/.cache
whitelist ${HOME}/.npm
memory-deny-write-execute
restrict-namespaces
```

(`noprofile` is a firejail CLI flag, not a profile directive, so generated
profiles omit it. X11 is disabled with `x11 none`, the profile syntax
firejail ≥ 0.9.80 accepts.)

Wrapper adds per-instance flags (see §5): `--blacklist="${HOME}/.herdr/worktrees"`,
`--noblacklist="${WORKTREE}"`, `--whitelist="${WORKTREE}"`,
`--netfilter=...`, `--env=HERDR_AGENT=...`, proxy URL with the task's
`BUDGET_ID` as userinfo, etc. The blacklist blocks sibling worktrees; the
per-worktree whitelist/noblacklist re-allows only the task's own checkout.

Agent-native second layer (defense in depth, provisioned per worktree) —
`environment/agent/claude-settings.json` (example):

```json
{
  "permissions": {
    "allow": ["Edit(./**)", "Bash(git *)", "Bash(npm run *)", "Bash(npm test *)", "WebFetch(domain:github.com)", "WebFetch(domain:api.anthropic.com)"],
    "ask": ["WebFetch"],
    "deny": ["Read(../**)", "Read(~/.ssh/**)", "Read(./.env*)", "Bash(sudo *)", "Bash(curl *)", "Bash(wget *)"]
  }
}
```

(The shipped `environment/agent/claude-settings.json` allows the full
default-template domain set; non-listed `WebFetch` domains fall to `ask`,
and the proxy still 403s anything off the template allowlist.)

plus `environment/agent/AGENTS.md.snippet` (advisory scope block).

Worktree provisioning must copy these files into each new worktree:
`bash environment/scripts/provision-worktree --worktree <WT>`
(Herdr plugin `tdi/herdr-worktree-setup` or
`arjenblokzijl/herdr-worktree-provisioner` can call it).

## 4. Network policy — allowlist + budget

Firejail netfilter `~/.config/firejail/herdr-netfilter.net`
(iptables-restore format). Blocks direct web, allows only loopback proxy:

```text
*filter
:INPUT DROP [0:0]
:FORWARD DROP [0:0]
:OUTPUT DROP [0:0]
-A INPUT -i lo -j ACCEPT
-A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
-A OUTPUT -o lo -j ACCEPT
-A OUTPUT -d 127.0.0.1 -p tcp --dport 8888 -j ACCEPT
-A OUTPUT -p udp --dport 53 -j ACCEPT
-A OUTPUT -p tcp --dport 53 -j ACCEPT
COMMIT
```

Proxy `herdr-web-proxy` (localhost only; one instance per env template, each on
its own loopback port — default 8888, strict 8889, offline 8890, web 8891,
node 8892, python 8893):

* `~/.config/herdr-web-proxy/allowlist[-<alias>].txt` — one domain per line
  (generated from the template's `network.allowlist`; validated to
  `example.com` / `*.example.com`, lowercase; bare `*`, schemes, ports and
  paths are rejected):
  ```text
  github.com
  *.githubusercontent.com
  registry.npmjs.org
  api.anthropic.com
  ```
* Env per agent: `BUDGET_ID=<workspace-id>`, `BUDGET_MAX=200`. The wrapper
  encodes `BUDGET_ID` in the proxy-URL userinfo
  (`http://<budget-id>@127.0.0.1:<port>`), which stock tools forward as
  `Proxy-Authorization: Basic ...` on every request including `CONNECT`.
  The proxy decodes that first, then `X-Budget-Id`, then last-seen-per-IP,
  then its `--budget-id` default — so tasks sharing a template proxy are
  still accounted separately.
* Behavior: `CONNECT host:443` / `GET http://host/` → check allowlist →
  check `count[BUDGET_ID] < BUDGET_MAX` → forward or `403 domain-not-allowed` /
  `429 budget-exhausted`. Append JSONL to `~/.local/state/herdr-web-proxy/access.log`.
  `Proxy-Authorization` is stripped before forwarding upstream.
* Wrapper exports `http_proxy` / `https_proxy` (upper + lower case) with the
  encoded userinfo URL.
  Also deny `Bash(curl --noproxy *)` via agent settings since curl could bypass env.

## 5. Herdr integration — parallel worktrees

```bash
# once
herdr plugin install tdi/herdr-worktree-setup
mkdir -p ~/.config/firejail ~/.config/herdr-web-proxy ~/.local/state/herdr-web-proxy
./init.sh  # renders all templates + symlinks profiles/netfilters/allowlists

# per task (from main checkout)
herdr worktree create --branch feat/auth --no-focus
herdr worktree list --cwd ~/code/myrepo
bash environment/scripts/provision-worktree --worktree ~/.herdr/worktrees/myrepo/feat-auth
# one proxy per template you use:
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --port 8888 --allowlist environment/proxy/config/allowlist.txt --budget-max 200 &
```

Wrapper `environment/scripts/herdr-agent-firejail` (sketch):

```bash
#!/usr/bin/env bash
# usage: herdr-agent-firejail [--template ALIAS] --worktree PATH [--kind claude] [--budget-id w2] [--budget-max 200] -- <agent args...>
# --template picks environment/templates/<alias>.json (default: default);
# explicit --kind/--budget-max/--proxy-port override the template defaults.
# --list-templates / --show-template inspect the resolved profile, netfilter,
# allowlist, and proxy command without launching anything.
set -euo pipefail
WORKTREE=""; KIND="claude"; BUDGET_ID="default"; BUDGET_MAX="200"; TEMPLATE="default"; PROXY_PORT=""
while [[ $# -gt 0 ]]; do case "$1" in
  --template|--env-template) TEMPLATE="$2"; shift 2;;
  --proxy-port) PROXY_PORT="$2"; shift 2;;
  --list-templates|--show-template) echo "(inspect mode)"; shift;;
  --worktree) WORKTREE="$2"; shift 2;;
  --kind) KIND="$2"; shift 2;;
  --budget-id) BUDGET_ID="$2"; shift 2;;
  --budget-max) BUDGET_MAX="$2"; shift 2;;
  --) shift; break;;
  *) echo "bad arg $1" >&2; exit 2;;
esac; done
exec firejail \
  --profile="$HOME/.config/firejail/herdr-agent.profile" \
  --name="herdr-$(basename "$WORKTREE")-$$" \
  --blacklist="$HOME/.herdr/worktrees" \
  --noblacklist="$WORKTREE" \
  --whitelist="$WORKTREE" \
  --netfilter="$HOME/.config/firejail/herdr-netfilter.net" \
  --env=HERDR_AGENT="$KIND" \
  --env=HERDR_WORKTREE="$WORKTREE" \
  --env=BUDGET_ID="$BUDGET_ID" \
  --env=BUDGET_MAX="$BUDGET_MAX" \
  --env=http_proxy=http://"$BUDGET_ID"@127.0.0.1:8888 \
  --env=https_proxy=http://"$BUDGET_ID"@127.0.0.1:8888 \
  --env=HTTP_PROXY=http://"$BUDGET_ID"@127.0.0.1:8888 \
  --env=HTTPS_PROXY=http://"$BUDGET_ID"@127.0.0.1:8888 \
  --env=NO_PROXY=localhost,127.0.0.1 \
  "$@"
```

(The real wrapper resolves `--template <alias>` to per-template profile /
netfilter / allowlist / port, URL-encodes `BUDGET_ID` into the proxy URL,
and supports `--list-templates` / `--show-template`. See
`environment/scripts/herdr-agent-firejail`.)
```

Start agent in a Herdr pane (keeps detection via `HERDR_AGENT`):

```bash
WORKTREE="$(pwd)"
bash environment/scripts/herdr-agent-firejail --template default --worktree "$WORKTREE" --budget-id w-feat-auth --budget-max 200 -- claude
```

Cleanup: `herdr worktree remove --workspace <child-id>` (runs `git worktree
remove`, keeps branch). Reset proxy counter per task when done.

## 6. Acceptance tests (must pass before use)

1. `F1` Outside read blocked: `cat ~/.ssh/id_rsa` inside jail → `Permission denied`.
2. `F2` Sibling worktree invisible: `ls ~/.herdr/worktrees/myrepo/<other-branch>` → denied / empty.
3. `F3` Worktree R/W works: `touch $HERDR_WORKTREE/.sandbox-ok && rm $HERDR_WORKTREE/.sandbox-ok`.
4. `N1` Allowed domain works: `curl -x http://127.0.0.1:8888 https://github.com -I` → 200.
5. `N2` Denied domain blocked: `curl -x http://127.0.0.1:8888 https://evil.example -I` → 403.
6. `N3` Direct bypass blocked: `curl --noproxy '*' https://github.com -I --max-time 5` → timeout / DROP.
7. `B1` Budget enforced: `BUDGET_MAX=3`, 4th fetch → 429, log shows `budget-exhausted`.
8. `P1` Parallel: two jails on two worktrees simultaneously, `F1–B1` hold in both, distinct `BUDGET_ID`s in log.
9. `H1` Herdr still classifies pane: `herdr agent list` shows agent with correct kind/state.

## 7. Limitations / ops notes

* Firejail needs user namespaces; on hardened kernels enable `unprivileged_userns_clone`. No `sudo` inside jail by design.
* `--private-etc` list is minimal — test `npm install` / `git clone https://` after changes.
* Package managers need cache whitelists (§3) or every worktree re-downloads the world.
* Proxy is HTTP/HTTPS only. Raw TCP/UDP/ICMP is DROPped — intentional. Allow `git@github.com:22` only via explicit netfilter exception if needed.
* Prompt text (`AGENTS.md`: "stay in repo, max N fetches") is advisory only; enforcement is firejail + proxy + agent `deny` rules.
* Logs: `firejail --audit`, proxy `access.log`, and `herdr pane read` together form the audit trail.

## 8. Build checklist

- [ ] `herdr` installed, `[worktrees] directory` set, test `worktree create/list/remove`. (manual, host-side)
- [x] Env template(s) defined via `bash environment/scripts/env-setup`
  (alias + Firejail drill-down); rendered profile/netfilter/allowlist installed
  (`init.sh` re-renders and links all of them). Shipped: default/strict/offline/web/node/python.
- [x] `herdr-web-proxy` + template `network.allowlist` + `budget_max_default`
  agreed; per-template ports/allowlists/budgets covered by pytest (live localhost proxy tests).
  Still verify live `N1–N3/B1` against real domains per template before use.
- [x] `herdr-agent-firejail` wrapper executable, `--template <alias>
  --show-template` resolves the right files (covered by pytest).
  `H1` detection still needs a live Herdr host to verify.
- [x] `environment/agent/claude-settings.json` + `AGENTS.md.snippet` committed;
  `scripts/provision-worktree` copies them (wire into worktree-setup plugin).
- [ ] `F1–F3/P1` live firejail runs with 2 concurrent worktrees (needs user
  namespaces + real host; container overlayfs cannot whitelist `/usr`).
