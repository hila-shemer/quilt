import pytest
from quilt.db import DB
from quilt import gates, scheduler

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
def cfg(tmp_path):
    p = tmp_path / "quilt.toml"
    p.write_text(CFG)
    return gates.load_config(p)


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "q.sqlite3")


def test_tick_happy_path(repo_with_branches, db, cfg):
    report = scheduler.tick(repo_with_branches.path, db, cfg)
    # 3 combos; all merge clean; gates run on each
    assert report["probed"] == 3
    assert report["gated"] == 3
    assert report["queued"] == 0


def test_tick_routes_conflicts(repo_with_branches, db, cfg):
    r = repo_with_branches
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")
    report = scheduler.tick(r.path, db, cfg)
    assert report["queued"] == 2          # both combos containing feat-conflict
    assert report["gated"] == 1


def test_unvalidated_serialization(db, tmp_path, repo_with_branches):
    # Three clean branches -> pairs {a,b} and {a,c} share untested subset {a}.
    r = repo_with_branches
    r.branch("feat-clean2")
    r.commit_file("feature2.txt", "another feature\n")
    r.git("checkout", "-q", "main")
    cfg3 = tmp_path / "q3.toml"
    cfg3.write_text(CFG.replace(
        '["feat-clean", "feat-conflict"]',
        '["feat-clean", "feat-clean2", "feat-conflict"]'))
    report = scheduler.tick(r.path, db, gates.load_config(cfg3), heavy_k=1)
    assert report["deferred"] >= 1        # one heavy slot per untested resolution
