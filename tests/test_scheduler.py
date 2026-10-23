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


CFG_PROMO = CFG + """
[promotion]
target = "main"
candidate_gate = "compiles"
final_gate = "t4day"
final_cmd = "test -f base.txt"
"""

# compiles passes and tests does not, so a clean merge is not a pass: the
# unsoundness the short-circuit must not be built on.
CFG_TWO_GATE = """
[quilt]
base = "main"
branches = ["feat-clean", "feat-conflict"]

[[gate]]
name = "compiles"
cmd = "test -f base.txt"

[[gate]]
name = "tests"
cmd = "false"

[targets]
main = "tests"

[promotion]
target = "main"
candidate_gate = "tests"
final_gate = "promote"
final_cmd = "test -f base.txt"
"""


def _cfg_at(tmp_path, text, name="q.toml"):
    p = tmp_path / name
    p.write_text(text)
    return gates.load_config(p)


def test_tick_happy_path(repo_with_branches, db, cfg):
    report = scheduler.tick(repo_with_branches.path, db, cfg)
    # No [promotion] section, so no candidate gate: nothing to short-circuit
    # on, and today's 3 combos are probed and gated as they always were.
    assert report["probed"] == 3
    assert report["gated"] == 3
    assert report["queued"] == 0
    assert report["short_circuit"] is False


def test_tick_short_circuits_when_the_full_set_clears_the_candidate_gate(
        repo_with_branches, db, tmp_path):
    """The size-N set clears the gate, so the other 2^N-2 are never probed.

    Every other combination is a strict subset of it, and `freeze` picks the
    largest ready merge-point: the full set is the unique maximum, so no subset
    probe can tie or beat it and none of them is worth the merge or the gate.
    """
    r = repo_with_branches
    cfg = _cfg_at(tmp_path, CFG_PROMO)
    report = scheduler.tick(r.path, db, cfg)
    assert report["short_circuit"] is True
    assert report["probed"] == 1
    assert report["gated"] == 1
    # the skip is real: no subset row was ever recorded
    assert [mp["member_branches"] for mp in db.list_merge_points()] == \
        [["feat-clean", "feat-conflict"]]


def test_the_pool_is_idle_after_the_short_circuit(repo_with_branches, db, tmp_path):
    """The short-circuit's other half, and the bug it would otherwise be:
    `schedulable` re-enumerates the power set, so the 2 unprobed subsets of this
    pool read as pending forever and `quilt idle` never says idle — the tick
    and the idle predicate have to agree about a set that was never probed."""
    r = repo_with_branches
    cfg = _cfg_at(tmp_path, CFG_PROMO)
    assert scheduler.tick(r.path, db, cfg)["short_circuit"] is True
    assert len(db.list_merge_points()) == 1     # the 2 singles were never probed
    assert scheduler.schedulable(r.path, db, cfg) == []


def test_tick_falls_back_when_the_full_set_fails_the_candidate_gate(
        repo_with_branches, db, tmp_path):
    """Merging clean is not passing. The full set merges clean and clears
    `compiles`, but the candidate gate is `tests` and it fails it — so the tick
    must walk the whole power set exactly as before, and the subset rows must be
    there for the poison cascade and the failure bisect to use."""
    r = repo_with_branches
    cfg = _cfg_at(tmp_path, CFG_TWO_GATE)
    report = scheduler.tick(r.path, db, cfg)
    assert report["short_circuit"] is False
    assert report["probed"] == 3
    assert report["gated"] == 3          # each row cleared `compiles`
    assert len(db.list_merge_points()) == 3


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
