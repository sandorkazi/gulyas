"""Tests for bin/gulyas (project init / continue-or-error / run wiring)."""

import types
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_PATH = REPO_ROOT / "bin" / "gulyas"


def load_cli():
    loader = SourceFileLoader("gulyas_cli", str(CLI_PATH))
    spec = spec_from_loader("gulyas_cli", loader, origin=str(CLI_PATH))
    mod = module_from_spec(spec)
    mod.__file__ = str(CLI_PATH)
    loader.exec_module(mod)
    return mod


g = load_cli()
HOME = REPO_ROOT


def run_init(folder: Path, *extra: str) -> int:
    return g.main(["init", str(folder), *extra])


# -- init: new project -------------------------------------------------------
def test_init_creates_missing_folder_with_config_and_goal(tmp_path):
    folder = tmp_path / "my-task"  # does not exist yet
    assert run_init(folder) == 0
    text = (folder / "gulyas.yaml").read_text()
    assert "name: my-task" in text
    assert "template: default" in text
    assert "# budget_max:" in text  # defaults present but commented
    assert "# proxy_port:" in text
    assert (folder / "GOAL.md").read_text().startswith("# GOAL — my-task")
    assert not (folder / "AGENTS.md").exists()  # opt-in only


def test_init_with_agents_scaffolds_scope_with_marker(tmp_path):
    folder = tmp_path / "t"
    assert run_init(folder, "--with-agents") == 0
    body = (folder / "AGENTS.md").read_text()
    assert "Stay inside this worktree" in body  # keeps provision-worktree idempotent
    assert "GOAL.md" in body


def test_init_template_and_kind_flags_land_in_config(tmp_path):
    folder = tmp_path / "w"
    assert run_init(folder, "--template", "offline", "--kind", "opencode") == 0
    text = (folder / "gulyas.yaml").read_text()
    assert "template: offline" in text
    assert "kind: opencode" in text


def test_init_unknown_template_is_an_error(tmp_path):
    assert run_init(tmp_path / "x", "--template", "nope") == 2


# -- init: continue-or-error ---------------------------------------------------
def test_init_existing_project_errors_without_force(tmp_path):
    folder = tmp_path / "p"
    assert run_init(folder) == 0
    assert run_init(folder) == 2  # already a project: continue via run/status


def test_init_nonempty_nonproject_dir_errors(tmp_path):
    folder = tmp_path / "hijack"
    folder.mkdir()
    (folder / "notes.txt").write_text("someone else's files")
    assert run_init(folder) == 2


def test_init_force_scaffolds_in_place_without_overwriting(tmp_path):
    folder = tmp_path / "hijack"
    folder.mkdir()
    (folder / "notes.txt").write_text("keep me")
    assert run_init(folder, "--force") == 0
    assert (folder / "notes.txt").read_text() == "keep me"
    assert (folder / "gulyas.yaml").is_file()
    # second --force on an existing project fills nothing, overwrites nothing
    before = (folder / "gulyas.yaml").read_text()
    assert run_init(folder, "--force") == 0
    assert (folder / "gulyas.yaml").read_text() == before


# -- yaml subset ----------------------------------------------------------------
def test_yaml_comments_and_overrides(tmp_path):
    folder = tmp_path / "c"
    assert run_init(folder) == 0
    cfg_path = folder / "gulyas.yaml"
    cfg_path.write_text(cfg_path.read_text().replace("# budget_max: 200", "budget_max: 50"))
    cfg, errs = g.load_project(folder)
    assert errs == []
    assert cfg["environment"]["budget_max"] == 50
    assert cfg["environment"]["template"] == "default"  # active key survived


def test_yaml_bad_syntax_reported(tmp_path):
    folder = tmp_path / "b"
    assert run_init(folder) == 0
    (folder / "gulyas.yaml").write_text("this is not yaml mapping\n")
    _, errs = g.load_project(folder)
    assert errs


# -- run/status gating ----------------------------------------------------------
def test_run_and_status_require_a_project(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    assert g.main(["run", str(folder), "--", "claude"]) == 2
    assert g.main(["status", str(folder)]) == 2


def test_resolve_merges_template_defaults_and_overrides(tmp_path):
    folder = tmp_path / "r"
    assert run_init(folder) == 0
    cfg, errs = g.load_project(folder)
    assert errs == []
    r, err = g.resolve_project(HOME, folder, cfg, {})
    assert err is None
    assert (r["template"], r["proxy_port"], r["budget_max"]) == ("default", 8888, 200)
    assert r["budget_id"] == "r" and r["kind"] == "claude"
    r2, _ = g.resolve_project(HOME, folder, cfg, {"budget_max": 7, "kind": "opencode"})
    assert (r2["budget_max"], r2["kind"]) == (7, "opencode")


def test_build_run_command_wires_wrapper_flags(tmp_path):
    folder = tmp_path / "w2"
    assert run_init(folder) == 0
    cfg, _ = g.load_project(folder)
    r, _ = g.resolve_project(HOME, folder, cfg, {})
    cmd = g.build_run_command(HOME, folder, r, ["opencode", "--standalone"])
    assert cmd[:2] == [str(HOME / "environment" / "scripts" / "herdr-agent-firejail"), "--template"]
    assert "--worktree" in cmd and str(folder) in cmd
    assert cmd[-3:] == ["--", "opencode", "--standalone"]
    assert "--proxy-port" not in cmd  # template default: don't override
    r["port_overridden"] = True
    cmd2 = g.build_run_command(HOME, folder, r, ["claude"])
    assert "--proxy-port" in cmd2


def test_status_runs_and_reports(tmp_path, capsys):
    folder = tmp_path / "s"
    assert run_init(folder) == 0
    assert g.main(["status", str(folder)]) == 0
    out = capsys.readouterr().out
    assert "template:  default" in out and "budget:" in out
