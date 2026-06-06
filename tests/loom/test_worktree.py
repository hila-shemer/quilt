import pytest

from quilt import gitio
from quilt.loom.worktree import PoolExhausted, WorktreePool


def test_checkout_yields_worktree_at_tree(repo, tmp_path):
    pool = WorktreePool(repo.path, root=tmp_path / "wt", size=2)
    want = gitio.tree_of(repo.path, "main")
    with pool.checkout("main") as wt:
        assert gitio.tree_of(wt, "HEAD") == want
        assert (wt / "base.txt").exists()


def test_pool_raises_when_exhausted(repo, tmp_path):
    pool = WorktreePool(repo.path, root=tmp_path / "wt", size=1)
    with pool.checkout("main"):
        with pytest.raises(PoolExhausted):
            with pool.checkout("main", timeout=0.05):
                pass


def test_crash_reaps_worktree_and_releases_slot(repo, tmp_path):
    pool = WorktreePool(repo.path, root=tmp_path / "wt", size=1)
    with pytest.raises(ValueError):
        with pool.checkout("main"):
            raise ValueError("boom")
    # no leaked worktree entries (only the main checkout remains)
    listing = gitio.git(repo.path, "worktree", "list")
    assert str((tmp_path / "wt").resolve()) not in listing
    # slot was released → we can lease again
    with pool.checkout("main") as wt:
        assert (wt / "base.txt").exists()
