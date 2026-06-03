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
    out = resolve.try_mediate(repo.path, "main", db, mp_id)
    assert out is None                      # mediate can't fix both-edited line
    assert db.pending_work()[0]["kind"] == "conflict"


def test_reuse_blocked_when_poison(conflicted):
    repo, db, mp_id = conflicted
    db.set_validation(mp_id, "poison")
    assert resolve.reusable_resolution(repo.path, db, mp_id) is None


def test_reuse_returns_pinned_ref(repo_with_branches, db):
    res = probe.probe_all(repo_with_branches.path, "main", ["feat-clean"], db)
    mp_id = res[0]["id"]
    sha = resolve.reusable_resolution(repo_with_branches.path, db, mp_id)
    assert sha == gitio.read_ref(repo_with_branches.path, f"refs/quilt/{mp_id}")
