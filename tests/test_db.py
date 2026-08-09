import pytest
from quilt.db import DB


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "quilt.sqlite3")


def test_merge_point_roundtrip(db):
    db.upsert_merge_point(id="mp1", base_tree_sha="t", base_commit_sha="c",
                          member_patch_ids=["p1", "p2"], member_tips=["s1", "s2"],
                          construction="clean")
    mp = db.get_merge_point("mp1")
    assert mp["construction"] == "clean"
    assert mp["validation_state"] == "untested"
    assert mp["member_patch_ids"] == ["p1", "p2"]


def test_gate_status_keyed_by_base(db):
    db.upsert_merge_point(id="mp1", base_tree_sha="t", base_commit_sha="c1",
                          member_patch_ids=["p1"], member_tips=["s1"],
                          construction="clean")
    db.record_gate("mp1", "compiles", "c1", "pass")
    assert db.gate_result("mp1", "compiles", "c1") == "pass"
    # staleness = absence of row for the new base
    assert db.gate_result("mp1", "compiles", "c2") is None


def test_highest_gate_derived(db):
    db.upsert_merge_point(id="mp1", base_tree_sha="t", base_commit_sha="c1",
                          member_patch_ids=["p1"], member_tips=["s1"],
                          construction="clean")
    db.record_gate("mp1", "compiles", "c1", "pass")
    db.record_gate("mp1", "triton", "c1", "pass")
    db.record_gate("mp1", "mllib_ut", "c1", "fail")
    assert db.highest_gate("mp1", "c1", ["compiles", "triton", "mllib_ut", "t4h"]) == "triton"


def test_poison_cascades_to_supersets(db):
    for id_, members in [("a", ["p1"]), ("ab", ["p1", "p2"]), ("b", ["p2"])]:
        db.upsert_merge_point(id=id_, base_tree_sha="t", base_commit_sha="c",
                              member_patch_ids=members, member_tips=members,
                              construction="clean")
    db.set_validation("a", "poison")
    assert db.get_merge_point("ab")["validation_state"] == "untested"   # reset
    assert db.get_merge_point("b")["validation_state"] == "untested"    # untouched
    assert db.get_merge_point("a")["validation_state"] == "poison"


def _mp(db, id_="mp1", members=("p1",)):
    db.upsert_merge_point(id=id_, base_tree_sha="t", base_commit_sha="c",
                          member_patch_ids=list(members), member_tips=list(members),
                          construction="clean")


def test_work_state_transitions(db):
    _mp(db)
    db.enqueue_work("conflict", "mp1", "boom")
    item = db.pending_work()[0]
    db.set_work_state(item["id"], "triaged")
    assert db.pending_work() == []
    assert db.work_by_state("triaged")[0]["id"] == item["id"]


def test_work_by_state_filters_kind(db):
    _mp(db)
    db.enqueue_work("conflict", "mp1")
    db.enqueue_work("test_fail", "mp1")
    for item in db.pending_work():
        db.set_work_state(item["id"], "triaged")
    assert len(db.work_by_state("triaged")) == 2
    assert len(db.work_by_state("triaged", kind="conflict")) == 1


def test_triage_roundtrip(db):
    db.record_triage("1", "mp1", "conflict", "rename collision", "moderate",
                     model="stub")
    row = db.get_triage("1")
    assert row["effort_class"] == "moderate"
    assert row["est_cause"] == "rename collision"
    assert db.get_triage("missing") is None


def test_fix_roundtrip(db):
    _mp(db)
    fix_id = db.add_fix("mp1", "refs/quilt/fix/mp1", ["tip1", "tip2"])
    [fix] = db.list_fixes(state="pending")
    assert fix["id"] == fix_id
    assert fix["affected_tips"] == ["tip1", "tip2"]
    db.set_fix_state(fix_id, "offered")
    assert db.list_fixes(state="pending") == []
    assert db.list_fixes(state="offered")[0]["backprop_state"] == "offered"


def test_candidate_roundtrip(db):
    _mp(db)
    cand_id = db.add_candidate("main", "mp1", "deadbeef")
    cand = db.active_candidate("main")
    assert cand["id"] == cand_id
    assert cand["commit_sha"] == "deadbeef"
    assert db.active_candidate("other") is None
    db.set_candidate_state(cand_id, "promoted")
    assert db.active_candidate("main") is None


def test_opening_a_pre_migration_db_adds_the_new_columns(tmp_path):
    """An existing .quilt.sqlite3 must survive an upgrade of quilt itself."""
    path = tmp_path / "old.sqlite3"
    db = DB(path)
    db.conn.execute("ALTER TABLE work_queue DROP COLUMN gate")
    db.conn.execute("ALTER TABLE merge_point DROP COLUMN member_branches")
    db.conn.commit()
    db.conn.close()

    reopened = DB(path)
    work_cols = {r["name"] for r in
                 reopened.conn.execute("PRAGMA table_info(work_queue)")}
    mp_cols = {r["name"] for r in
               reopened.conn.execute("PRAGMA table_info(merge_point)")}
    assert "gate" in work_cols
    assert "member_branches" in mp_cols
