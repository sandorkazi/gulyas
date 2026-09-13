"""Unit tests for allowlist matching + budget store (no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from herdr_web_proxy import BudgetStore, host_allowed


def test_host_allowed_exact_and_wildcard():
    patterns = ["github.com", "*.githubusercontent.com"]
    assert host_allowed("github.com", patterns)
    assert host_allowed("GITHUB.COM", patterns)
    assert host_allowed("raw.githubusercontent.com", patterns)
    assert not host_allowed("githubusercontent.com", patterns)  # bare domain not matched by '*.'
    assert not host_allowed("evil.example", patterns)


def test_budget_store_counts_and_reset(tmp_path):
    store = BudgetStore(tmp_path)
    ok, count = store.check_and_consume("task-a", 2)
    assert (ok, count) == (True, 1)
    ok, count = store.check_and_consume("task-a", 2)
    assert (ok, count) == (True, 2)
    ok, count = store.check_and_consume("task-a", 2)
    assert ok is False and count == 2
    store.reset("task-a")
    ok, count = store.check_and_consume("task-a", 2)
    assert (ok, count) == (True, 1)
