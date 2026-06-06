"""P3 Task 4: loom CLI — promote + propose-push."""
import itertools

import pytest

from quilt import gates, gitio
from quilt.db import DB
from quilt.loom import cli, increments, linearize, milestone, pushgate, schema
from quilt.loom.increments import Increment
from quilt.loom.worktree import WorktreePool

_age = itertools.count()

_TOML = """
[quilt]
base = "main"
branches = []

[[gate]]
name = "build"
test = false
cmd = "true"

[promotion]
target = "next_staging"

[promotion.stress]
name = "long"
test = false
cmd = "true"
"""


def make_inc(repo, db, id, fname, content):
    repo.git("checkout", "-q", "-b", f"b-{id}", "main")
    sha = repo.commit_file(fname, content, f"inc {id}")
    repo.git("checkout", "-q", "main")
    inc = Increment(id=id, patches={"self": sha}, age=next(_age))
    increments.add(db, inc)
    return inc


def test_cli_promote_advances_next_staging(repo, tmp_path):
    dbfile = tmp_path / "q.sqlite3"
    d = DB(dbfile)
    schema.apply(d.conn)
    toml = tmp_path / "quilt.toml"
    toml.write_text(_TOML)
    cfg = gates.load_config(toml)
    a = make_inc(repo, d, "a", "a.txt", "A\n")
    b = make_inc(repo, d, "b", "b.txt", "B\n")
    linearize.solve(repo.path, d, cfg, [a, b],
                    WorktreePool(repo.path, root=tmp_path / "wt", size=4))

    cli.main(["--repo", str(repo.path), "--config", str(toml),
              "--db", str(dbfile), "promote"])

    first = milestone.milestones(repo.path, d, cfg)[0]
    assert gitio.read_ref(repo.path, milestone.NEXT_STAGING_REF) == first


def test_cli_propose_push_prints_and_pushes_nothing(repo, tmp_path, capsys):
    dbfile = tmp_path / "q.sqlite3"
    DB(dbfile)
    toml = tmp_path / "quilt.toml"
    toml.write_text(_TOML)
    repo.branch("work")
    tip = repo.commit_file("f.txt", "x\n")
    repo.git("checkout", "-q", "main")
    gitio.update_ref(repo.path, pushgate.NEXT_STAGING_REF, tip)

    cli.main(["--repo", str(repo.path), "--config", str(toml), "--db", str(dbfile),
              "propose-push", "git@github.com:hila-shemer/e2.git",
              "refs/loom/next_staging"])

    out = capsys.readouterr().out
    assert "hila-shemer" in out and tip[:12] in out
    assert gitio.git(repo.path, "remote") == ""        # nothing was pushed


def test_cli_propose_push_emits_patches_on_block(repo, tmp_path, capsys):
    dbfile = tmp_path / "q.sqlite3"
    DB(dbfile)
    toml = tmp_path / "quilt.toml"
    toml.write_text(_TOML)
    repo.branch("work")
    tip = repo.commit_file("f.txt", "x\n")
    repo.git("checkout", "-q", "main")
    gitio.update_ref(repo.path, pushgate.NEXT_STAGING_REF, tip)

    cli.main(["--repo", str(repo.path), "--config", str(toml), "--db", str(dbfile),
              "propose-push", "git@github.com:Majestic/e2.git",
              "refs/loom/next_staging", "--outdir", str(tmp_path / "patches")])

    out = capsys.readouterr().out
    assert ".patch" in out                              # artifact paths printed
    assert list((tmp_path / "patches").glob("*.patch"))
