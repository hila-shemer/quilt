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
