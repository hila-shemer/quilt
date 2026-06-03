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


def test_promote_advances_target(repo_with_branches, cfgfile, capsys):
    run(["tick"], repo_with_branches, cfgfile, capsys)
    out = run(["promote", "next"], repo_with_branches, cfgfile, capsys)
    assert "next" in out
    assert repo_with_branches.git("rev-parse", "refs/quilt/target/next")
