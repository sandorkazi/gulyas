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
                      "private-dev", "disable-mnt", "nox11",
                      "dbus-user none", "restrict-namespaces",
                      "blacklist ${HOME}/.ssh", "whitelist /usr"):
        assert directive in out, directive


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
    assert "--dport 8888" in net  # proxy loopback still reachable


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
    try:
        paths = write_artifacts(src)
        assert paths["profile"].read_text() == render_profile(src)
        assert paths["netfilter"].read_text() == render_netfilter(src)
    finally:
        _clean_repo_artifacts("tmp-demo")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, **kw)


def test_wrapper_list_and_show_template():
    r = _run(["bash", "environment/scripts/herdr-agent-firejail", "--list-templates"])
    assert r.returncode == 0 and "default" in r.stdout
    for alias in list_templates():
        r = _run(["bash", "environment/scripts/herdr-agent-firejail",
                  "--template", alias, "--show-template"])
        assert r.returncode == 0, alias
    r = _run(["bash", "environment/scripts/herdr-agent-firejail",
              "--template", "default", "--show-template"])
    assert "herdr-agent.profile" in r.stdout and "--port 8888" in r.stdout
    r = _run(["bash", "environment/scripts/herdr-agent-firejail",
              "--template", "no-such", "--show-template"])
    assert r.returncode != 0 and "env-setup" in r.stderr


def test_env_setup_lists_existing_templates():
    r = _run(["python3", "environment/scripts/env-setup", "--list"])
    assert r.returncode == 0 and "default" in r.stdout
    r = _run(["python3", "environment/scripts/env-setup", "--show", "default"])
    assert r.returncode == 0 and '"alias": "default"' in r.stdout


def test_interview_creates_aliased_template(tmp_path, monkeypatch):
    # Simulate: action=new, alias, description, then Enter-keeps for everything, save=yes.
    monkeypatch.setenv("GULYAS_TEMPLATES_DIR", str(tmp_path))
    (tmp_path / "default.json").write_text(
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
    env = {**os.environ, "GULYAS_TEMPLATES_DIR": str(tmp_path)}
    try:
        r = subprocess.run([sys.executable, "environment/scripts/env-setup"],
                           input=answers, capture_output=True, text=True,
                           cwd=REPO_ROOT, env=env, timeout=60)
        assert r.returncode == 0, r.stderr
        created = tmp_path / "web-strict.json"
        assert created.exists(), r.stdout
        t = json.loads(created.read_text())
        assert t["alias"] == "web-strict"
        # interview must have started with existing templates
        assert "found 1 template" in r.stdout
    finally:
        _clean_repo_artifacts("web-strict")
