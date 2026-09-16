"""Tests for env templates: schema, rendering, wrapper wiring, interview flow."""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(SCRIPTS))

from lib_env_template import (  # noqa: E402
    artifact_paths,
    is_valid_alias,
    list_templates,
    load_template,
    render_allowlist,
    render_netfilter,
    render_profile,
    validate_template,
    write_artifacts,
)


def test_default_template_valid():
    t = load_template("default")
    assert validate_template(t) == []
    assert t["network"]["proxy_port"] == 8888


def test_alias_rules():
    assert is_valid_alias("default")
    assert is_valid_alias("web-strict_2")
    assert not is_valid_alias("Web")
    assert not is_valid_alias("has space")
    assert not is_valid_alias("")


def test_render_profile_covers_firejail_drilldown():
    t = load_template("default")
    out = render_profile(t)
    for directive in ("noroot", "nonewprivs", "seccomp", "private-tmp",
                      "private-dev", "disable-mnt", "x11 none",
                      "dbus-user none", "restrict-namespaces",
                      "blacklist ${HOME}/.ssh", "whitelist /usr"):
        assert directive in out, directive
    assert "nox11" not in out  # invalid profile syntax (firejail wants `x11 none`)
    assert "noprofile" not in out  # CLI-only flag, must not appear in profile file


def test_render_netfilter_locks_egress_to_proxy():
    t = load_template("default")
    out = render_netfilter(t)
    assert "--dport 8888" in out
    assert "--dport 53" in out  # DNS allowed
    assert ":OUTPUT DROP" in out


def test_render_allowlist_matches_proxy_config():
    t = load_template("default")
    rendered = render_allowlist(t)
    on_disk = (REPO_ROOT / "environment" / "proxy" / "config" / "allowlist.txt").read_text()
    assert rendered == on_disk
    assert "github.com" in rendered


def test_default_template_ungated():
    t = load_template("default")
    out = render_profile(t)
    assert "private-bin" not in out
    assert "whitelist /bin" in out


def test_strict_template_tool_gate():
    t = load_template("strict")
    assert validate_template(t) == []
    out = render_profile(t)
    assert "private-bin bash,sh,env,git,python3,node,npm,ls,cat,grep,sed,find" in out
    assert "whitelist /bin" not in out  # private-bin scopes /bin instead
    assert "blacklist ${HOME}/.docker" in out


def test_offline_template_denies_everything():
    t = load_template("offline")
    assert validate_template(t) == []
    assert t["network"]["allowlist"] == []
    net = render_netfilter(t)
    assert "--dport 53" not in net  # no DNS in-jail
    assert f"--dport {t['network']['proxy_port']}" in net  # proxy loopback still reachable


def test_shipped_templates_have_unique_ports():
    ports = {}
    for alias in list_templates():
        t = load_template(alias)
        port = t["network"]["proxy_port"]
        assert port not in ports.values(), f"port {port} shared by {ports} and {alias}"
        ports[alias] = port
    assert ports["default"] == 8888


def test_all_shipped_templates_render_clean():
    """Checked-in profile/net/allowlist must equal what the JSON renders."""
    for alias in list_templates():
        t = load_template(alias)
        assert validate_template(t) == [], alias
        paths = artifact_paths(alias)
        assert paths["profile"].read_text() == render_profile(t), alias
        assert paths["netfilter"].read_text() == render_netfilter(t), alias
        assert paths["allowlist"].read_text() == render_allowlist(t), alias


def test_artifact_paths_default_vs_named():
    d = artifact_paths("default")
    assert d["profile"].name == "herdr-agent.profile"
    assert d["netfilter"].name == "herdr-netfilter.net"
    assert d["allowlist"].name == "allowlist.txt"
    c = artifact_paths("web-strict")
    assert c["profile"].name == "herdr-agent-web-strict.profile"
    assert c["netfilter"].name == "herdr-netfilter-web-strict.net"
    assert c["allowlist"].name == "allowlist-web-strict.txt"


def test_validate_rejects_bad_template():
    t = load_template("default")
    t["network"]["proxy_port"] = 99999
    assert any("proxy_port" in e for e in validate_template(t))
    t = load_template("default")
    t["alias"] = "Bad Alias!"
    assert any("alias" in e for e in validate_template(t))
    t = load_template("default")
    t["filesystem"]["allowed_tools"] = ["/bin/evil"]
    assert any("allowed_tools" in e for e in validate_template(t))
    for bad in ("*", "*.", "https://evil.com", "evil.com:443", "evil com",
                "EVIL.COM", "*.", "**.example.com"):
        t = load_template("default")
        t["network"]["allowlist"] = [bad]
        assert any("allowlist" in e for e in validate_template(t)), bad
    t = load_template("default")
    t["network"]["direct_egress"] = True
    assert any("direct_egress" in e for e in validate_template(t))
    t = load_template("default")
    t["filesystem"]["noblacklist"] = [""]
    assert any("noblacklist" in e for e in validate_template(t))


def _clean_repo_artifacts(*aliases):
    for a in aliases:
        for p in artifact_paths(a).values():
            try:
                if p.exists() and a != "default":
                    p.unlink()
            except OSError:
                pass


def test_write_artifacts_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("GULYAS_TEMPLATES_DIR", str(tmp_path))
    src = json.loads((REPO_ROOT / "environment" / "templates" / "default.json").read_text())
    src["alias"] = "tmp-demo"
    (tmp_path / "tmp-demo.json").write_text(json.dumps(src))
    # Isolated: render into tmp repo root so the real checkout stays clean.
    fake_root = tmp_path / "repo"
    (fake_root / "environment" / "firejail").mkdir(parents=True)
    (fake_root / "environment" / "proxy" / "config").mkdir(parents=True)
    paths = write_artifacts(src, repo_root=fake_root)
    assert paths["profile"].read_text() == render_profile(src)
    assert paths["netfilter"].read_text() == render_netfilter(src)
    assert paths["allowlist"].read_text() == render_allowlist(src)


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, **kw)


def test_wrapper_list_and_show_template():
    r = _run(["bash", "environment/scripts/herdr-agent-firejail", "--list-templates"])
    assert r.returncode == 0 and "default" in r.stdout
    for alias in list_templates():
        r = _run(["bash", "environment/scripts/herdr-agent-firejail",
                  "--template", alias, "--show-template"])
        assert r.returncode == 0, alias
        assert "--budget-max" in r.stdout and "proxy_url:" in r.stdout
    r = _run(["bash", "environment/scripts/herdr-agent-firejail",
              "--template", "default", "--show-template"])
    assert "herdr-agent.profile" in r.stdout and "--port 8888" in r.stdout
    r = _run(["bash", "environment/scripts/herdr-agent-firejail",
              "--template", "strict", "--show-template"])
    assert "--port 8889" in r.stdout and "allowlist-strict.txt" in r.stdout
    r = _run(["bash", "environment/scripts/herdr-agent-firejail",
              "--template", "no-such", "--show-template"])
    assert r.returncode != 0 and "env-setup" in r.stderr


def test_wrapper_encodes_budget_and_sibling_blacklist():
    r = _run(["bash", "-c",
              "bash environment/scripts/herdr-agent-firejail --template default "
              "--worktree /tmp/wt-demo --budget-id 'task a/b' --budget-max 7 --show-template"])
    assert r.returncode == 0
    assert "task%20a%2Fb:" in r.stdout  # userinfo URL-encoded (id:secret form)
    assert "<redacted>" in r.stdout  # show mode never prints the real secret
    assert "--blacklist" in r.stdout or "sibling isolation" in r.stdout
    assert "registered at launch" in r.stdout  # per-task cap is enforced, not advisory
    # dry-run the real firejail argv via bash -x? Instead check script text.
    text = (REPO_ROOT / "environment" / "scripts" / "herdr-agent-firejail").read_text()
    assert '--blacklist="$HOME/.herdr/worktrees"' in text
    assert '--noblacklist="$WORKTREE"' in text
    assert "PROXY_USER" in text and "PROXY_PASS" in text and "register_task" in text


def test_wrapper_register_only_writes_registry(tmp_path):
    state = tmp_path / "state"
    r = _run(["bash", "environment/scripts/herdr-agent-firejail",
              "--template", "default",
              "--budget-id", "reg-task", "--budget-max", "7",
              "--budget-secret", "s3cr3t", "--state-dir", str(state),
              "--register-only"])
    assert r.returncode == 0, r.stderr
    assert "registered budget 'reg-task' max=7" in r.stdout
    assert "<redacted>" in r.stdout
    assert "s3cr3t" not in r.stdout  # secret never echoed
    reg = json.loads((state / "tasks.json").read_text())
    assert reg == {"reg-task": {"max": 7, "secret": "s3cr3t"}}
    # invalid max is rejected before writing
    r = _run(["bash", "environment/scripts/herdr-agent-firejail",
              "--template", "default",
              "--budget-id", "reg-task", "--budget-max", "0",
              "--state-dir", str(state), "--register-only"])
    assert r.returncode != 0


def test_env_setup_lists_existing_templates():
    r = _run(["python3", "environment/scripts/env-setup", "--list"])
    assert r.returncode == 0 and "default" in r.stdout
    r = _run(["python3", "environment/scripts/env-setup", "--show", "default"])
    assert r.returncode == 0 and '"alias": "default"' in r.stdout


def test_interview_creates_aliased_template(tmp_path, monkeypatch):
    # Simulate: action=new, alias, description, then Enter-keeps for everything, save=yes.
    # Layout mirrors <root>/environment/templates so artifacts render under tmp (no repo pollution).
    tdir = tmp_path / "environment" / "templates"
    tdir.mkdir(parents=True)
    monkeypatch.setenv("GULYAS_TEMPLATES_DIR", str(tdir))
    (tdir / "default.json").write_text(
        (REPO_ROOT / "environment" / "templates" / "default.json").read_text())
    answers = "\n".join([
        "new",            # action
        "web-strict",     # alias (set when creating)
        "strict web env",  # description
        "",               # strictness preset (keep)
        "", "", "", "", "", "", "", "", "", "",  # 10 yes/no keeps
        "",               # blacklist keep
        "",               # caches keep
        "",               # toolchain keep
        "",               # read-only dirs keep
        "",               # noblacklist keep
        "",               # allowed tools keep (no gate)
        "",               # private-etc keep
        "",               # proxy port keep
        "",               # allowlist keep
        "",               # budget keep
        "",               # dns keep
        "",               # agent kind keep
        "",               # deny-outside keep
        "y",              # save?
    ]) + "\n"
    env = {**os.environ, "GULYAS_TEMPLATES_DIR": str(tdir)}
    r = subprocess.run([sys.executable, "environment/scripts/env-setup"],
                       input=answers, capture_output=True, text=True,
                       cwd=REPO_ROOT, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    created = tdir / "web-strict.json"
    assert created.exists(), r.stdout
    t = json.loads(created.read_text())
    assert t["alias"] == "web-strict"
    # interview must have started with existing templates
    assert "found 1 template" in r.stdout
    # artifacts rendered into the isolated root, not the real checkout
    assert (tmp_path / "environment" / "firejail" / "herdr-agent-web-strict.profile").exists()
    assert not (REPO_ROOT / "environment" / "firejail" / "herdr-agent-web-strict.profile").exists()


def test_provision_worktree_copies_scope(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    r = _run(["bash", "environment/scripts/provision-worktree", "--worktree", str(wt)])
    assert r.returncode == 0, r.stderr
    assert (wt / ".claude" / "settings.json").exists()
    assert "Stay inside this worktree" in (wt / "AGENTS.md").read_text()
    # idempotent: re-run does not duplicate the snippet
    r = _run(["bash", "environment/scripts/provision-worktree", "--worktree", str(wt)])
    assert r.returncode == 0
    assert (wt / "AGENTS.md").read_text().count("Stay inside this worktree") == 1
