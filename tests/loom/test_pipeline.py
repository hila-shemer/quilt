"""P4 Task 3: single-branch end-to-end pipeline (harvest→linearize→stage→promote)."""
import pytest

from quilt import gates, gitio
from quilt.db import DB
from quilt.loom import cli, linearize, milestone, pipeline, schema
from quilt.loom.worktree import WorktreePool
from tests.conftest import make_stub

TEST_GLOBS = ["tests/**"]


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "q.sqlite3")
    schema.apply(d.conn)
    return d


@pytest.fixture
def pool(repo, tmp_path):
    return WorktreePool(repo.path, root=tmp_path / "wt", size=4)


def _cfg(llm=None):
    return gates.Config(
        base="main", branches=[],
        gates=[{"name": "build", "test": False, "cmd": "true"}],
        targets={},
        llm=llm or {},
        promotion={"target": "next_staging",
                   "stress": {"name": "long", "test": False, "cmd": "true"}})


def _tree_paths(repo, ref):
    return gitio.git(repo.path, "ls-tree", "-r", "--name-only", ref).splitlines()


def test_single_branch_flows_harvest_to_next_staging(repo, db, pool, tmp_path):
    marker = tmp_path / "llm-called"
    stub = make_stub(tmp_path, "llm.sh",
                     f'#!/bin/sh\ntouch {marker}\ncat >/dev/null\necho \'{{}}\'\n')
    cfg = _cfg(llm={"audit_cmd": str(stub), "seam_cmd": str(stub)})

    repo.branch("feat")
    repo.commit_file("tests/test_x.py", "def test_x(): pass\n")
    repo.commit_file("src/feature.py", "y = 2\n")
    repo.git("checkout", "-q", "main")

    res = pipeline.run(repo.path, db, cfg, "feat", TEST_GLOBS, pool)

    # 1. test-only commit harvested to base
    assert res["lifted"] and "tests/test_x.py" in _tree_paths(repo, "main")
    # 2. staging is the maximal green prefix — here the full (single-feature) series
    sol = res["solution"]
    assert sol.seam is None and len(sol.landed) == 1
    staging = gitio.read_ref(repo.path, linearize.STAGING_REF)
    assert "src/feature.py" in _tree_paths(repo, staging)
    # 3. milestone cleared candidate gate and next_staging fast-forwarded after stress
    assert res["promotion"]["promoted"] is True
    assert gitio.read_ref(repo.path, milestone.NEXT_STAGING_REF) == staging
    # 4. ZERO LLM calls on the clean path
    assert not marker.exists()


def test_cli_run_drives_the_pipeline(repo, db, tmp_path):
    dbfile = tmp_path / "q.sqlite3"
    d = DB(dbfile)
    schema.apply(d.conn)
    toml = tmp_path / "quilt.toml"
    toml.write_text(
        '[quilt]\nbase = "main"\nbranches = []\n\n'
        '[[gate]]\nname = "build"\ntest = false\ncmd = "true"\n\n'
        '[harvest]\ntest_globs = ["tests/**"]\n\n'
        '[promotion]\ntarget = "next_staging"\n\n'
        '[promotion.stress]\nname = "long"\ntest = false\ncmd = "true"\n')

    repo.branch("feat")
    repo.commit_file("tests/test_x.py", "def test_x(): pass\n")
    repo.commit_file("src/feature.py", "y = 2\n")
    repo.git("checkout", "-q", "main")

    cli.main(["--repo", str(repo.path), "--config", str(toml),
              "--db", str(dbfile), "run", "--branch", "feat"])

    assert "tests/test_x.py" in _tree_paths(repo, "main")
    assert gitio.read_ref(repo.path, milestone.NEXT_STAGING_REF) is not None
