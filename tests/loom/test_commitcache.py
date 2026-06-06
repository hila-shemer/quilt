import pytest

from quilt import gates, gitio
from quilt.db import DB
from quilt.loom import commitcache, schema
from quilt.loom.worktree import WorktreePool


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "q.sqlite3")
    schema.apply(d.conn)
    return d


@pytest.fixture
def pool(repo, tmp_path):
    return WorktreePool(repo.path, root=tmp_path / "wt", size=2)


def cfg(gate_dicts):
    return gates.Config(base="main", branches=[], gates=gate_dicts, targets={})


def test_caches_pass_and_skips_rerun(repo, db, pool, tmp_path):
    counter = tmp_path / "count"
    gate = {"name": "build", "test": False,
            "cmd": f"echo x >> {counter}; test -f base.txt"}
    c = cfg([gate])
    assert commitcache.run_commit_gate(repo.path, db, c, "main", gate, pool) == "pass"
    assert commitcache.run_commit_gate(repo.path, db, c, "main", gate, pool) == "pass"
    # second call was a cache hit → gate body ran only once
    assert counter.read_text().count("x") == 1


def test_rerun_when_tree_changes(repo, db, pool, tmp_path):
    counter = tmp_path / "count"
    gate = {"name": "build", "test": False,
            "cmd": f"echo x >> {counter}; true"}
    c = cfg([gate])
    commitcache.run_commit_gate(repo.path, db, c, "main", gate, pool)
    repo.commit_file("more.txt", "data\n")          # new tree
    commitcache.run_commit_gate(repo.path, db, c, "main", gate, pool)
    assert counter.read_text().count("x") == 2      # different tree → re-ran


def test_identical_trees_share_cache(repo, db, pool, tmp_path):
    counter = tmp_path / "count"
    gate = {"name": "build", "test": False, "cmd": f"echo x >> {counter}; true"}
    c = cfg([gate])
    # dup: a new commit with the SAME tree as main but a different sha
    dup = gitio.commit_tree(repo.path, gitio.tree_of(repo.path, "main"),
                            parents=[gitio.rev(repo.path, "main")], msg="dup")
    commitcache.run_commit_gate(repo.path, db, c, "main", gate, pool)
    commitcache.run_commit_gate(repo.path, db, c, dup, gate, pool)
    assert counter.read_text().count("x") == 1      # same tree → cache hit on dup


def test_fake_green_not_cached_and_requeued(repo, db, pool):
    # a TEST gate that exits 0 but reports zero tests against expected=1
    gate = {"name": "unit", "test": True, "expected": 1, "cmd": "echo '0 passed'"}
    c = cfg([gate])
    assert commitcache.run_commit_gate(repo.path, db, c, "main", gate, pool) == "fail"
    tree = gitio.tree_of(repo.path, "main")
    assert commitcache.commit_gate_result(db, tree, "unit") is None   # not cached
    assert db.pending_work()[0]["kind"] == "test_fail"


def test_run_ladder_stops_at_first_fail(repo, db, pool):
    c = cfg([
        {"name": "build", "test": False, "cmd": "true"},
        {"name": "unit", "test": False, "cmd": "false"},
        {"name": "stress", "test": False, "cmd": "true"},
    ])
    assert commitcache.run_ladder_on_commit(repo.path, db, c, "main", pool) == "build"
    assert db.pending_work()[0]["kind"] == "test_fail"
