"""Regression: quilt's commit-creating git calls must not depend on a host git
identity. gitio.GIT_ENV supplies a fixed synthetic committer so porcelain (merge,
commit, cherry-pick) works on a bare CI checkout with no user.name/email set."""
import subprocess

from quilt import gitio


def _git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, env=env)


def test_git_env_supplies_committer_when_host_has_none(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    # Isolate from any host/global identity, and force git to refuse the
    # auto-detected fallback identity so an unconfigured commit deterministically
    # fails (otherwise modern git fabricates user@host and the hazard is hidden).
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\tuseConfigOnly = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
              "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(k, raising=False)

    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "-A")

    # Hazard is real here: a commit with no configured/usable identity fails.
    bare = _git(repo, "commit", "-m", "x")
    assert bare.returncode != 0, "test setup: expected committer-identity failure"

    # The fix: gitio.GIT_ENV carries the synthetic identity, so the commit lands.
    ok = _git(repo, "commit", "-m", "x", env=gitio.GIT_ENV)
    assert ok.returncode == 0, ok.stderr

    author = _git(repo, "log", "-1", "--format=%an <%ae> / %cn <%ce>",
                  env=gitio.GIT_ENV).stdout.strip()
    assert author == "quilt <quilt@localhost> / quilt <quilt@localhost>"


def test_quilt_git_calls_use_the_synthetic_identity(tmp_path, monkeypatch):
    """The wrappers, not just the constant, must carry GIT_ENV — a commit made
    through gitio on a host with no identity has to land and be attributed."""
    repo = tmp_path / "repo"
    repo.mkdir()

    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\tuseConfigOnly = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
              "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(gitio, "GIT_ENV", {
        **gitio.GIT_ENV,
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_SYSTEM": "/dev/null",
    })

    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "-A")
    tree = gitio.git(repo, "write-tree")

    sha = gitio.commit_tree(repo, tree, [], "quilt: test commit")
    author = gitio.git(repo, "log", "-1", "--format=%an <%ae>", sha)
    assert author == "quilt <quilt@localhost>"
