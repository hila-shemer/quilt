import pytest
from quilt.db import DB
from quilt import gitio, probe, resolve


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "q.sqlite3")


@pytest.fixture
def conflicted(repo_with_branches, db):
    r = repo_with_branches
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")
    results = probe.probe_all(r.path, "main", ["feat-conflict"], db)
    assert results[0]["construction"] == "conflict"
    return r, db, results[0]["id"]


def test_mediate_fails_on_semantic_conflict(conflicted):
    repo, db, mp_id = conflicted
    out = resolve.try_mediate(repo.path, db, mp_id)
    assert out is None                      # mediate can't fix both-edited line
    assert db.pending_work()[0]["kind"] == "conflict"


def test_reusable_resolution_unknown_mp(db, tmp_path):
    assert resolve.reusable_resolution(tmp_path, db, "nonexistent-mp-id") is None


def test_reuse_blocked_when_poison(repo_with_branches, db):
    """A pinned resolution is reusable, and poisoning the merge-point takes that
    away.

    The ref must exist before the poison, or the assertion holds for the wrong
    reason. A *conflicted* merge-point writes no ref at all (probe_combo returns
    no commit), so `reusable_resolution` is None there whether or not the poison
    gate exists - which is what this test used to assert against."""
    res = probe.probe_all(repo_with_branches.path, "main", ["feat-clean"], db)
    mp_id = res[0]["id"]
    assert resolve.reusable_resolution(repo_with_branches.path, db, mp_id) is not None
    db.set_validation(mp_id, "poison")
    assert resolve.reusable_resolution(repo_with_branches.path, db, mp_id) is None


def test_reuse_returns_pinned_ref(repo_with_branches, db):
    res = probe.probe_all(repo_with_branches.path, "main", ["feat-clean"], db)
    mp_id = res[0]["id"]
    sha = resolve.reusable_resolution(repo_with_branches.path, db, mp_id)
    assert sha == gitio.read_ref(repo_with_branches.path, f"refs/quilt/{mp_id}")


def test_evict_removes_superset_ref_and_leaves_poisoned_ref_intact(repo_with_branches, db):
    """I1: poisoning a subset evicts the superset's ref but not the poisoned one's."""
    # probe_all creates both a 1-member point and a 2-member point
    results = probe.probe_all(repo_with_branches.path, "main",
                              ["feat-clean", "feat-conflict"], db)
    by_members = {len(r["branches"]): r for r in results}
    solo_id = by_members[1]["id"]  # 1-member (subset) — to be poisoned
    pair_id = by_members[2]["id"]  # 2-member (superset)

    # both have refs before poison
    assert gitio.read_ref(repo_with_branches.path, f"refs/quilt/{pair_id}") is not None

    # poison the 1-member point: DB returns cascade ids
    cascade_ids = db.set_validation(solo_id, "poison")
    # evict git refs for all cascade-reset merge-points
    resolve.evict(repo_with_branches.path, cascade_ids)

    # superset ref deleted → reusable_resolution is None
    assert gitio.read_ref(repo_with_branches.path, f"refs/quilt/{pair_id}") is None
    assert resolve.reusable_resolution(repo_with_branches.path, db, pair_id) is None

    # poisoned point's own ref still exists (but reusable_resolution still None due to poison)
    assert gitio.read_ref(repo_with_branches.path, f"refs/quilt/{solo_id}") is not None
    assert resolve.reusable_resolution(repo_with_branches.path, db, solo_id) is None


def test_poison_merge_point_evicts_supersets(repo_with_branches, db):
    r = repo_with_branches
    results = probe.probe_all(r.path, "main", ["feat-clean", "feat-conflict"], db)
    by_n = sorted(results, key=lambda x: len(x["branches"]))
    single, pair = by_n[0]["id"], by_n[-1]["id"]
    assert gitio.read_ref(r.path, f"refs/quilt/{pair}")
    cascade = resolve.poison_merge_point(r.path, db, single)
    assert pair in cascade
    assert db.get_merge_point(single)["validation_state"] == "poison"
    assert gitio.read_ref(r.path, f"refs/quilt/{pair}") is None
