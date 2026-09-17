"""Tutorial regression tests: backlog <-> loop <-> wrapper stay consistent.

Covers `tutorial/tdd-mobile/` without running the full 15-step loop:
- every backlog task has exactly one RED snippet and one GREEN source version;
- RED fails against the stub, GREEN passes fully (real pytest, tmp copies);
- the wrapper resolves the netfilter to a regular file (Firejail rejects
  symlinks: "invalid network filter file").
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TUT = REPO_ROOT / "tutorial" / "tdd-mobile"
WRAPPER = REPO_ROOT / "environment" / "scripts" / "herdr-agent-firejail"


def load_loop():
    spec = importlib.util.spec_from_file_location(
        "tutorial_agentic_loop", TUT / "agentic_loop.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backlog_snippets_sources_agree():
    tasks = json.loads((TUT / "tasks.json").read_text())
    loop = load_loop()
    ids = [t["id"] for t in tasks]
    assert ids, "backlog is empty"
    assert set(loop.TEST_SNIPPETS) == set(ids), "snippet keys != backlog ids"
    assert len(loop.FULL_SOURCES) == len(ids) + 1, \
        "need stub v0 + one GREEN version per task"


def _stage(tmp_path, source_version, snippet_ids):
    loop = load_loop()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "src" / "todo_store.py").write_text(loop.FULL_SOURCES[source_version])
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text(
        (TUT / "tests" / "conftest.py").read_text())
    header = loop.TEST_HEADER
    (tmp_path / "tests" / "test_todo_store.py").write_text(
        header + "".join(loop.TEST_SNIPPETS[i] for i in snippet_ids))


def _pytest(tmp_path):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_todo_store.py", "-q"],
        cwd=tmp_path, capture_output=True, text=True)


def test_red_fails_against_stub(tmp_path):
    tasks = json.loads((TUT / "tasks.json").read_text())
    _stage(tmp_path, 0, [tasks[0]["id"]])
    p = _pytest(tmp_path)
    assert p.returncode != 0, "RED must fail against the v0 stub"
    assert "1 failed" in p.stdout


def test_green_passes_full_suite(tmp_path):
    tasks = json.loads((TUT / "tasks.json").read_text())
    loop = load_loop()
    _stage(tmp_path, len(tasks), [t["id"] for t in tasks])
    assert len(loop.FULL_SOURCES) == len(tasks) + 1
    p = _pytest(tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    assert f"{len(tasks)} passed" in p.stdout


def test_wrapper_netfilter_is_regular_file():
    p = subprocess.run(
        ["bash", str(WRAPPER), "--template", "offline", "--show-template"],
        capture_output=True, text=True, cwd=REPO_ROOT)
    assert p.returncode == 0, p.stderr
    line = next(l for l in p.stdout.splitlines() if l.startswith("netfilter:"))
    nf = Path(line.split(":", 1)[1].strip())
    assert nf.is_file() and not nf.is_symlink(), \
        f"firejail rejects symlink netfilters, got: {nf}"
