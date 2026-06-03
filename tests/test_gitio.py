import subprocess
import pytest
from quilt import gitio


# ---------------------------------------------------------------------------
# GitError tests (new behaviour)
# ---------------------------------------------------------------------------

def test_merge_tree_bad_ref_raises_git_error(repo):
    """merge_tree with a non-existent branch must raise GitError, not silently
    return a MergeResult(clean=False, tree="")."""
    with pytest.raises(gitio.GitError):
        gitio.merge_tree(repo.path, "no-such-branch", "main")


def test_patch_id_empty_diff_raises_git_error(repo):
    """patch_id for a branch with no commits beyond main raises GitError."""
    repo.branch("empty-branch")           # identical to main, no new commits
    repo.git("checkout", "-q", "main")
    with pytest.raises(gitio.GitError, match="empty diff"):
        gitio.patch_id(repo.path, "main", "empty-branch")


# ---------------------------------------------------------------------------
# Existing tests (must keep passing)
# ---------------------------------------------------------------------------

def test_patch_id_stable_across_metadata(repo_with_branches):
    r = repo_with_branches
    pid1 = gitio.patch_id(r.path, "main", "feat-clean")
    # Recommit feat-clean with a different message/date — patch-id must not move.
    r.git("checkout", "-q", "feat-clean")
    r.git("commit", "--amend", "-m", "different message",
          "--date", "2001-01-01T00:00:00")
    pid2 = gitio.patch_id(r.path, "main", "feat-clean")
    assert pid1 == pid2


def test_merge_tree_clean(repo_with_branches):
    res = gitio.merge_tree(repo_with_branches.path, "feat-clean", "main")
    assert res.clean
    assert res.tree


def test_merge_tree_conflict(repo_with_branches):
    r = repo_with_branches
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")
    res = gitio.merge_tree(r.path, "feat-conflict", "main")
    assert not res.clean
    assert "base.txt" in res.conflict_files


def test_commit_tree_and_refs(repo_with_branches):
    r = repo_with_branches
    res = gitio.merge_tree(r.path, "feat-clean", "main")
    sha = gitio.commit_tree(r.path, res.tree,
                            parents=[gitio.rev(r.path, "main"),
                                     gitio.rev(r.path, "feat-clean")],
                            msg="merge")
    gitio.update_ref(r.path, "refs/quilt/test", sha)
    assert gitio.read_ref(r.path, "refs/quilt/test") == sha
    assert gitio.read_ref(r.path, "refs/quilt/missing") is None
