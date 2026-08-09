import pytest
from quilt.db import DB
from quilt import probe, gitio, resolve


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


def test_probe_all_raises_on_empty_diff(repo, db):
    """A branch that has no commits ahead of base produces an empty diff;
    probe_all must raise ValueError whose message names the branch."""
    repo.branch("empty-branch")
    repo.git("checkout", "-q", "main")
    with pytest.raises(ValueError, match="empty-branch"):
        probe.probe_all(repo.path, "main", ["empty-branch"], db)


def test_probe_marks_conflicts(repo_with_branches, db):
    r = repo_with_branches
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")
    results = probe.probe_all(r.path, "main", ["feat-clean", "feat-conflict"], db)
    by_members = {tuple(sorted(x["branches"])): x for x in results}
    assert by_members[("feat-conflict",)]["construction"] == "conflict"
    assert by_members[("feat-clean",)]["construction"] == "clean"


def test_reprobe_does_not_clobber_agent_resolution(repo_with_branches, db):
    """M1: re-probing a combo that already has a pinned non-poison resolution preserves it."""
    r = repo_with_branches
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")

    # First probe: feat-conflict produces conflict
    results = probe.probe_all(r.path, "main", ["feat-clean", "feat-conflict"], db)
    by_members = {tuple(sorted(x["branches"])): x for x in results}
    conflict_id = by_members[("feat-conflict",)]["id"]
    assert by_members[("feat-conflict",)]["construction"] == "conflict"

    # Simulate agent resolution: pin a ref and upsert construction='agent'
    # Use feat-clean's commit as a dummy resolved commit
    dummy_commit = gitio.rev(r.path, "feat-clean")
    dummy_tree = gitio.tree_of(r.path, dummy_commit)
    gitio.update_ref(r.path, f"refs/quilt/{conflict_id}", dummy_commit)
    conflict_mp = db.get_merge_point(conflict_id)
    db.upsert_merge_point(
        id=conflict_id,
        base_tree_sha=conflict_mp["base_tree_sha"],
        base_commit_sha=conflict_mp["base_commit_sha"],
        member_patch_ids=conflict_mp["member_patch_ids"],
        member_tips=conflict_mp["member_tips"],
        construction="agent",
        result_commit=dummy_commit,
        result_tree=dummy_tree,
    )
    assert resolve.reusable_resolution(r.path, db, conflict_id) == dummy_commit

    # Re-probe same branches
    results2 = probe.probe_all(r.path, "main", ["feat-clean", "feat-conflict"], db)
    by_members2 = {tuple(sorted(x["branches"])): x for x in results2}

    # construction must still be 'agent', not overwritten to 'conflict'
    assert by_members2[("feat-conflict",)]["construction"] == "agent"
    # pinned ref still intact
    assert resolve.reusable_resolution(r.path, db, conflict_id) == dummy_commit


def test_probe_records_member_branch_names(repo_with_branches, db):
    """A merge-point knows which branches it is about — no join required."""
    results = probe.probe_all(repo_with_branches.path, "main",
                              ["feat-clean", "feat-conflict"], db)
    pair = next(r for r in results if len(r["branches"]) == 2)
    mp = db.get_merge_point(pair["id"])
    assert mp["member_branches"] == ["feat-clean", "feat-conflict"]
