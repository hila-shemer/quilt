"""P4 Task 1: regression-lock harvest (spec §6.3)."""
import pytest

from quilt import gates, gitio
from quilt.db import DB
from quilt.loom import epoch, harvest, schema
from quilt.loom.worktree import WorktreePool

TEST_GLOBS = ["tests/**"]


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "q.sqlite3")
    schema.apply(d.conn)
    return d


@pytest.fixture
def pool(repo, tmp_path):
    return WorktreePool(repo.path, root=tmp_path / "wt", size=2)


def cfg(cand_cmd="true"):
    return gates.Config(base="main", branches=[],
                        gates=[{"name": "build", "test": False, "cmd": cand_cmd}],
                        targets={})


def _tree_paths(repo, ref):
    return gitio.git(repo.path, "ls-tree", "-r", "--name-only", ref).splitlines()


def _range(repo, lo, hi):
    out = gitio.git(repo.path, "rev-list", f"{lo}..{hi}")
    return [c for c in out.splitlines() if c.strip()]


def test_lifts_test_only_passing_commit(repo, db, pool):
    repo.branch("feat")
    repo.commit_file("tests/test_x.py", "def test_x(): pass\n")
    repo.git("checkout", "-q", "main")

    lifted = harvest.run(repo.path, db, cfg(), "feat", TEST_GLOBS, pool)

    assert len(lifted) == 1
    assert "tests/test_x.py" in _tree_paths(repo, "main")    # merged into base
    assert _range(repo, "main", "feat") == []                # donor drained (rebased)


def test_skips_non_test_commit(repo, db, pool):
    repo.branch("feat")
    repo.commit_file("src/x.py", "x = 1\n")
    repo.git("checkout", "-q", "main")

    lifted = harvest.run(repo.path, db, cfg(), "feat", TEST_GLOBS, pool)

    assert lifted == []
    assert "src/x.py" not in _tree_paths(repo, "main")       # base untouched
    assert len(_range(repo, "main", "feat")) == 1            # commit left on donor


def test_test_only_red_on_base_is_queued_not_lifted(repo, db, pool):
    # ladder fails whenever the test file is present → red on base.
    c = cfg(cand_cmd="! test -f tests/test_x.py")
    repo.branch("feat")
    repo.commit_file("tests/test_x.py", "def test_x(): pass\n")
    repo.git("checkout", "-q", "main")
    base_before = gitio.rev(repo.path, "main")

    lifted = harvest.run(repo.path, db, c, "feat", TEST_GLOBS, pool)

    assert lifted == []
    assert gitio.rev(repo.path, "main") == base_before       # base unchanged
    assert any(w["kind"] == "test_fail" for w in db.pending_work())


def test_batches_two_lifts_in_one_epoch_roll(repo, db, pool):
    repo.branch("feat")
    repo.commit_file("tests/test_a.py", "def test_a(): pass\n")
    repo.commit_file("tests/test_b.py", "def test_b(): pass\n")
    repo.git("checkout", "-q", "main")
    before = epoch.current(db)

    lifted = harvest.run(repo.path, db, cfg(), "feat", TEST_GLOBS, pool)

    assert len(lifted) == 2
    assert epoch.current(db) - before == 1                   # one boundary, not two
    paths = _tree_paths(repo, "main")
    assert "tests/test_a.py" in paths and "tests/test_b.py" in paths


def test_mixed_branch_lifts_only_the_test_commit(repo, db, pool):
    repo.branch("feat")
    repo.commit_file("tests/test_x.py", "def test_x(): pass\n")
    repo.commit_file("src/feature.py", "y = 2\n")
    repo.git("checkout", "-q", "main")

    lifted = harvest.run(repo.path, db, cfg(), "feat", TEST_GLOBS, pool)

    assert len(lifted) == 1
    assert "tests/test_x.py" in _tree_paths(repo, "main")
    assert "src/feature.py" not in _tree_paths(repo, "main")  # feature left behind
    # donor keeps the feature commit, replayed onto the new base
    assert len(_range(repo, "main", "feat")) == 1
    assert "src/feature.py" in _tree_paths(repo, "feat")
