import pytest
from quilt import cli

CFG = """
[quilt]
base = "main"
branches = ["feat-clean", "feat-conflict"]

[[gate]]
name = "compiles"
cmd = "test -f base.txt"

[targets]
next = "compiles"
"""


@pytest.fixture
def cfgfile(tmp_path):
    p = tmp_path / "quilt.toml"
    p.write_text(CFG)
    return p


def run(args, repo, cfgfile, capsys):
    cli.main(["--repo", str(repo.path), "--config", str(cfgfile)] + args)
    return capsys.readouterr().out


def test_tick_and_status(repo_with_branches, cfgfile, capsys):
    run(["tick"], repo_with_branches, cfgfile, capsys)
    out = run(["status"], repo_with_branches, cfgfile, capsys)
    assert "clean" in out
    assert "compiles" in out


def test_tick_output_key_value(repo_with_branches, cfgfile, capsys):
    """tick should print key=value pairs, not a dict repr."""
    out = run(["tick"], repo_with_branches, cfgfile, capsys)
    assert "probed=" in out
    assert "gated=" in out
    assert "queued=" in out
    assert "deferred=" in out
    # Must not look like a dict repr
    assert "{" not in out
    assert "}" not in out


def test_promote_advances_target(repo_with_branches, cfgfile, capsys):
    run(["tick"], repo_with_branches, cfgfile, capsys)
    out = run(["promote", "next"], repo_with_branches, cfgfile, capsys)
    assert "next" in out
    assert repo_with_branches.git("rev-parse", "refs/quilt/target/next")


def test_promote_unknown_target_exits_1(repo_with_branches, cfgfile, capsys):
    """Promoting an unknown target prints a message and exits 1."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--repo", str(repo_with_branches.path),
                  "--config", str(cfgfile),
                  "promote", "no-such-target"])
    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "unknown target" in out
    assert "no-such-target" in out


def test_promote_no_ready_candidate_exits_1(repo_with_branches, cfgfile, capsys):
    """Promoting when no merge point is ready exits 1 (no tick run yet)."""
    with pytest.raises(SystemExit) as exc_info:
        run(["promote", "next"], repo_with_branches, cfgfile, capsys)
    assert exc_info.value.code == 1
