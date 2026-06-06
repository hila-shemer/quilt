"""P1 Task 2/3/4 — test-integrity auditor."""
import sqlite3

import pytest

from quilt.loom import audit, schema


# ---- Task 2: deterministic checks ------------------------------------------

def test_real_green_passes(gate_run):
    v = audit.audit(gate_run(), db=None, cfg=None)
    assert v.real_green and not v.inconclusive


def test_zero_tests_is_fake_green(gate_run):
    v = audit.audit(gate_run(stdout="0 passed", expected_tests=5), None, None)
    assert not v.real_green and not v.inconclusive
    assert "no tests" in v.reason or "0" in v.reason


def test_nonzero_exit_is_fake_green(gate_run):
    v = audit.audit(gate_run(exit_code=1), None, None)
    assert not v.real_green
    assert "exit" in v.reason


def test_all_skipped_is_fake_green(gate_run):
    v = audit.audit(gate_run(stdout="0 passed, 5 skipped", expected_tests=None), None, None)
    assert not v.real_green
    assert "skip" in v.reason.lower()


def test_coverage_misses_diff_is_fake_green(gate_run):
    v = audit.audit(gate_run(coverage_paths=["b.c"], diff_paths=["a.c"]), None, None)
    assert not v.real_green
    assert "coverage" in v.reason.lower()


def test_count_mismatch_is_fake_green(gate_run):
    v = audit.audit(gate_run(stdout="3 passed", expected_tests=5), None, None)
    assert not v.real_green
    assert "expected" in v.reason.lower() or "3" in v.reason


def test_built_tree_mismatch_is_fake_green(gate_run):
    v = audit.audit(gate_run(tree_sha="t1", built_tree_sha="t2"), None, None)
    assert not v.real_green
    assert "tree" in v.reason.lower()


def test_verdict_persisted_when_db_given(gate_run, tmp_path):
    conn = sqlite3.connect(tmp_path / "q.sqlite3")
    schema.apply(conn)
    v = audit.audit(gate_run(subject_id="mpX", gate="unit", tree_sha="tt"), db=_DBShim(conn), cfg=None)
    row = conn.execute(
        "SELECT real_green, reason FROM audit_result WHERE subject_id='mpX' AND gate='unit' AND tree_sha='tt'"
    ).fetchone()
    assert row is not None and row[0] == int(v.real_green)


class _DBShim:
    """Minimal stand-in exposing only what audit() touches: a .conn."""
    def __init__(self, conn):
        self.conn = conn
