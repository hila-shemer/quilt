def test_fixture_branches(repo_with_branches):
    r = repo_with_branches
    assert set(r.git("branch", "--format=%(refname:short)").splitlines()) == {
        "main", "feat-clean", "feat-conflict",
    }
