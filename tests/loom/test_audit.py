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


# ---- Task 3: decision hook (Haiku) — only when inconclusive ----------------

from quilt import gates
from tests.conftest import make_stub

_NOVEL = dict(stdout="<<< custom harness blob with no recognizable summary >>>",
              expected_tests=None, coverage_paths=[], diff_paths=["a.c"])


def _cfg_with_audit(tmp_path, audit_cmd):
    p = tmp_path / "quilt.toml"
    p.write_text(f"""
[quilt]
base = "main"
branches = ["x"]

[llm]
audit_cmd = "{audit_cmd}"
""")
    return gates.load_config(p)


def test_inconclusive_consults_haiku(gate_run, tmp_path):
    stub = make_stub(tmp_path, "audit.sh",
                     '#!/bin/sh\ncat >/dev/null\n'
                     'echo \'{"real_green": false, "reason": "harness aborted"}\'\n')
    cfg = _cfg_with_audit(tmp_path, stub)
    v = audit.audit(gate_run(**_NOVEL), None, cfg)
    assert v.inconclusive and not v.real_green and "harness" in v.reason


def test_inconclusive_hook_can_pass(gate_run, tmp_path):
    stub = make_stub(tmp_path, "audit.sh",
                     '#!/bin/sh\ncat >/dev/null\n'
                     'echo \'{"real_green": true, "reason": "ran fine, novel format"}\'\n')
    cfg = _cfg_with_audit(tmp_path, stub)
    v = audit.audit(gate_run(**_NOVEL), None, cfg)
    assert v.inconclusive and v.real_green


def test_hook_llm_error_fails_closed(gate_run, tmp_path):
    stub = make_stub(tmp_path, "audit.sh", '#!/bin/sh\nexit 1\n')
    cfg = _cfg_with_audit(tmp_path, stub)
    v = audit.audit(gate_run(**_NOVEL), None, cfg)
    assert not v.real_green and v.inconclusive
    assert "error" in v.reason.lower()


def test_no_audit_cmd_fails_closed(gate_run):
    v = audit.audit(gate_run(**_NOVEL), None, None)
    assert not v.real_green and v.inconclusive


# ---- Task 4: gate-verification contract (void + requeue on fake-green) -----

from quilt.db import DB


def test_fake_green_voids_cache_and_requeues(gate_run, tmp_path):
    db = DB(tmp_path / "q.sqlite3")
    schema.apply(db.conn)
    db.upsert_merge_point(id="mp1", base_tree_sha="bt", base_commit_sha="c1",
                          member_patch_ids=["p1"], member_tips=["s1"],
                          construction="clean", result_commit="rc", result_tree="rt")
    db.record_gate("mp1", "unit", "c1", "pass")            # a (wrongly) green row
    run = gate_run(subject_id="mp1", gate="unit", exit_code=1)   # deterministic fake
    v = audit.verify_gate(repo=None, db=db, cfg=None, run=run)
    assert not v.real_green
    assert db.gate_result("mp1", "unit", "c1") == "fail"   # cache voided
    assert db.pending_work()[0]["kind"] == "test_fail"     # re-enqueued


def test_real_green_leaves_cache_intact(gate_run, tmp_path):
    db = DB(tmp_path / "q.sqlite3")
    schema.apply(db.conn)
    db.upsert_merge_point(id="mp1", base_tree_sha="bt", base_commit_sha="c1",
                          member_patch_ids=["p1"], member_tips=["s1"],
                          construction="clean", result_commit="rc", result_tree="rt")
    db.record_gate("mp1", "unit", "c1", "pass")
    v = audit.verify_gate(repo=None, db=db, cfg=None, run=gate_run(subject_id="mp1", gate="unit"))
    assert v.real_green
    assert db.gate_result("mp1", "unit", "c1") == "pass"
    assert db.pending_work() == []


def test_happy_path_never_calls_hook(gate_run, tmp_path):
    marker = tmp_path / "called"
    stub = make_stub(tmp_path, "audit.sh",
                     f'#!/bin/sh\ntouch {marker}\ncat >/dev/null\n'
                     'echo \'{"real_green": true, "reason": "x"}\'\n')
    cfg = _cfg_with_audit(tmp_path, stub)
    v = audit.audit(gate_run(), None, cfg)        # deterministic real-green
    assert v.real_green and not v.inconclusive
    assert not marker.exists()                    # zero LLM calls on the happy path
