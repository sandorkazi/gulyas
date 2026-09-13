# Environment — where Python runs and how to use it

Decision: **Python proxy runs on the host in `gulyas/.venv` by default.**
Docker holds an equivalent image only as an alternative (`environment/docker/`).

Why: Firejail's netfilter allows `127.0.0.1:8888` on loopback. A host `.venv`
proxy is reachable under that rule with zero port publishing. A container needs
`network_mode: host` to land on the same loopback, which is fine but adds a
moving part. Keep host default; container for CI / shared runners.

## Layout

```text
gulyas/
  .venv/                          # host python env (gitignored), created by setup.sh
  environment/
    README.md                     # this file
    proxy/src/herdr_web_proxy.py  # stdlib-only, no pip deps needed at runtime
    proxy/config/allowlist.txt    # agreed domains (symlinked to ~/.config/herdr-web-proxy/)
    proxy/tests/test_proxy.py     # pytest, runs in .venv
    firejail/herdr-agent.profile  # installed to ~/.config/firejail/
    firejail/herdr-netfilter.net  # iptables-restore, referenced by wrapper
    scripts/herdr-agent-firejail  # firejail wrapper, sets HERDR_AGENT + proxy env
    scripts/setup.sh              # creates .venv, installs test deps, links configs
    docker/Dockerfile.proxy       # alternative: same proxy in a container
    docker/compose.yml            # alternative runner (network_mode: host)
```

## Quickstart (host, recommended)

```bash
bash environment/scripts/setup.sh
.venv/bin/python environment/proxy/src/herdr_web_proxy.py --port 8888 &
.venv/bin/pytest environment/proxy/tests -q
bash environment/scripts/herdr-agent-firejail --worktree ~/.herdr/worktrees/myrepo/feat-x \
  --kind claude --budget-id feat-x --budget-max 200 -- claude
```

## Docker alternative

```bash
docker compose -f environment/docker/compose.yml up --build
# then run wrapper as usual (proxy still on 127.0.0.1:8888 via host network)
```

## Notes

* `herdr` binary is assumed per plan but not on PATH on this machine yet —
  `setup.sh` warns instead of failing so firejail/proxy work can proceed.
* Runtime proxy has no third-party imports; `requirements.txt` is only pytest.
* State + audit log: `~/.local/state/herdr-web-proxy/{budget.json,access.log}`.
