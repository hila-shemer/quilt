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


def test_enqueue_work_no_duplicates(repo_with_branches, db, cfg):
    """Repeated run_ladder on a failing gate must not enqueue duplicate test_fail rows."""
    [mp] = probe.probe_all(repo_with_branches.path, "main", ["feat-conflict"], db)
    gates.run_ladder(repo_with_branches.path, db, cfg, mp["id"])
    gates.run_ladder(repo_with_branches.path, db, cfg, mp["id"])
    work = db.pending_work()
    assert len(work) == 1
    assert work[0]["kind"] == "test_fail"


def test_ready_targets_returns_empty_when_poisoned(repo_with_branches, db, cfg):
    """I2: ready_targets returns [] when the merge-point is poisoned."""
    [mp] = probe.probe_all(repo_with_branches.path, "main", ["feat-clean"], db)
    gates.run_ladder(repo_with_branches.path, db, cfg, mp["id"])
    # confirm it passes normally first
    assert gates.ready_targets(db, cfg, mp["id"]) != []
    # now poison it
    db.set_validation(mp["id"], "poison")
    assert gates.ready_targets(db, cfg, mp["id"]) == []


def test_run_ladder_skips_worktree_when_all_cached(repo_with_branches, db, cfg, monkeypatch):
    """When all gates are already cached passes, no worktree should be created."""
    [mp] = probe.probe_all(repo_with_branches.path, "main", ["feat-clean"], db)
    # Run once to populate the cache.
    gates.run_ladder(repo_with_branches.path, db, cfg, mp["id"])

    # Patch worktree add to detect whether it is called on the second run.
    worktree_calls = []
    real_git = gates.gitio.git

    def spy_git(repo, *args, **kwargs):
        if args and args[0] == "worktree":
            worktree_calls.append(args)
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(gates.gitio, "git", spy_git)
    result = gates.run_ladder(repo_with_branches.path, db, cfg, mp["id"])
    assert result == "fast_tests"
    assert worktree_calls == [], "worktree should not be created when all gates are cached"


CFG_FULL = """
[quilt]
base = "main"
branches = ["feat-clean"]

[[gate]]
name = "compiles"
cmd = "test -f base.txt"

[[gate]]
name = "t4h"
cmd = "test -f feature.txt"
long = true

[targets]
local-stable = "t4h"

[llm]
triage_cmd = "/usr/bin/true"
resolve_cmd = "/usr/bin/true"

[promotion]
target = "main"
candidate_gate = "t4h"
final_gate = "t4day"
final_cmd = "test -f base.txt"
"""


def test_config_llm_and_promotion(tmp_path):
    p = tmp_path / "full.toml"
    p.write_text(CFG_FULL)
    c = gates.load_config(p)
    assert c.llm["triage_cmd"] == "/usr/bin/true"
    assert c.promotion["target"] == "main"
    assert c.promotion["final_gate"] == "t4day"
    assert c.gates[1].get("long") is True
    assert c.gates[0].get("long") is None


def test_config_llm_promotion_default_empty(cfg):
    assert cfg.llm == {}
    assert cfg.promotion == {}


CFG_LONG = """
[quilt]
base = "main"
branches = ["feat-clean"]

[[gate]]
name = "compiles"
cmd = "test -f base.txt"

[[gate]]
name = "t4h"
cmd = "test -f feature.txt"
long = true

[targets]
local-stable = "t4h"
"""


@pytest.fixture
def cfg_long(tmp_path):
    p = tmp_path / "long.toml"
    p.write_text(CFG_LONG)
    return gates.load_config(p)


def test_long_gate_pass_sets_validated(repo_with_branches, db, cfg_long):
    [mp] = probe.probe_all(repo_with_branches.path, "main", ["feat-clean"], db)
    assert gates.run_ladder(repo_with_branches.path, db, cfg_long, mp["id"]) == "t4h"
    assert db.get_merge_point(mp["id"])["validation_state"] == "validated"


def test_long_gate_fail_resets_untested(repo_with_branches, db, cfg_long):
    # feat-conflict lacks feature.txt → t4h fails
    [mp] = probe.probe_all(repo_with_branches.path, "main", ["feat-conflict"], db)
    assert gates.run_ladder(repo_with_branches.path, db, cfg_long, mp["id"]) == "compiles"
    assert db.get_merge_point(mp["id"])["validation_state"] == "untested"
    assert db.pending_work()[0]["kind"] == "test_fail"


def test_long_gate_never_overwrites_poison(repo_with_branches, db, cfg_long):
    [mp] = probe.probe_all(repo_with_branches.path, "main", ["feat-clean"], db)
    db.set_validation(mp["id"], "poison")
    gates.run_ladder(repo_with_branches.path, db, cfg_long, mp["id"])
    assert db.get_merge_point(mp["id"])["validation_state"] == "poison"


def test_short_gate_does_not_validate(repo_with_branches, db, cfg):
    [mp] = probe.probe_all(repo_with_branches.path, "main", ["feat-clean"], db)
    gates.run_ladder(repo_with_branches.path, db, cfg, mp["id"])
    assert db.get_merge_point(mp["id"])["validation_state"] == "untested"
