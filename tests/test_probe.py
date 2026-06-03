import pytest
from quilt.db import DB
from quilt import probe


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "q.sqlite3")


def test_rejects_more_than_five():
    with pytest.raises(ValueError):
        probe.enumerate_combos(["b1", "b2", "b3", "b4", "b5", "b6"])


def test_enumerates_power_set():
    combos = probe.enumerate_combos(["a", "b", "c"])
    assert len(combos) == 7  # 2^3 - 1


def test_probe_records_clean_and_conflict(repo_with_branches, db):
    results = probe.probe_all(repo_with_branches.path, "main",
                              ["feat-clean", "feat-conflict"], db)
    assert len(results) == 3
    by_members = {tuple(sorted(r["branches"])): r for r in results}
    assert by_members[("feat-clean",)]["construction"] == "clean"
    assert by_members[("feat-clean", "feat-conflict")]["construction"] == "clean"
    mp = db.get_merge_point(results[0]["id"])
    assert mp is not None


def test_probe_marks_conflicts(repo_with_branches, db):
    r = repo_with_branches
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")
    results = probe.probe_all(r.path, "main", ["feat-clean", "feat-conflict"], db)
    by_members = {tuple(sorted(x["branches"])): x for x in results}
    assert by_members[("feat-conflict",)]["construction"] == "conflict"
    assert by_members[("feat-clean",)]["construction"] == "clean"
