import pytest
from quilt.db import DB
from quilt import gates, probe

CFG = """
[quilt]
base = "main"
branches = ["feat-clean"]

[[gate]]
name = "compiles"
cmd = "test -f base.txt"

[[gate]]
name = "fast_tests"
cmd = "test -f feature.txt"

[targets]
next = "compiles"
local-stable = "fast_tests"
"""


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "quilt.toml"
    p.write_text(CFG)
    return gates.load_config(p)


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "q.sqlite3")


def test_ladder_order(cfg):
    assert cfg.ladder == ["compiles", "fast_tests"]


def test_run_gates_records_results(repo_with_branches, db, cfg):
    [mp] = probe.probe_all(repo_with_branches.path, "main", ["feat-clean"], db)
    passed = gates.run_ladder(repo_with_branches.path, db, cfg, mp["id"])
    assert passed == "fast_tests"
    base = db.get_merge_point(mp["id"])["base_commit_sha"]
    assert db.gate_result(mp["id"], "compiles", base) == "pass"


def test_ladder_stops_at_failure(repo_with_branches, db, cfg):
    [mp] = probe.probe_all(repo_with_branches.path, "main", ["feat-conflict"], db)
    # feat-conflict lacks feature.txt -> fast_tests fails, queued for triage
    passed = gates.run_ladder(repo_with_branches.path, db, cfg, mp["id"])
    assert passed == "compiles"
    assert db.pending_work()[0]["kind"] == "test_fail"


def test_ready_targets(repo_with_branches, db, cfg):
    [mp] = probe.probe_all(repo_with_branches.path, "main", ["feat-clean"], db)
    gates.run_ladder(repo_with_branches.path, db, cfg, mp["id"])
    assert gates.ready_targets(db, cfg, mp["id"]) == ["next", "local-stable"]
