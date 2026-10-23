import pytest
from quilt import candidate, gates, gitio, scheduler
from quilt.db import DB

CFG_PROMO = """
[quilt]
base = "main"
branches = ["feat-clean", "feat-conflict"]

[[gate]]
name = "compiles"
cmd = "test -f base.txt"

[targets]
next = "compiles"

[promotion]
target = "main"
candidate_gate = "compiles"
final_gate = "t4day"
final_cmd = "test -f base.txt"
"""


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "q.sqlite3")


def _cfg(tmp_path, text=CFG_PROMO, name="promo.toml"):
    p = tmp_path / name
    p.write_text(text)
    return gates.load_config(p)


@pytest.fixture
def gated(repo_with_branches, db, tmp_path):
    cfg = _cfg(tmp_path)
    scheduler.tick(repo_with_branches.path, db, cfg)
    return repo_with_branches, db, cfg


CFG_TWO_GATE = """
[quilt]
base = "main"
branches = ["feat-clean"]

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


def test_the_candidate_gate_bar_is_the_ladder_not_the_merge(
        repo_with_branches, db, tmp_path):
    """`clears_candidate_gate` is `freeze`'s own bar, and it is the only thing
    the short-circuit may test. Here the merge is clean and the candidate gate
    is `tests`, which failed: merge-clean is not passing, and a search that
    stopped on it would promote nothing where a subset would have passed."""
    cfg = _cfg(tmp_path, CFG_TWO_GATE, "two-gate.toml")
    scheduler.tick(repo_with_branches.path, db, cfg)
    [mp] = db.list_merge_points()
    assert mp["result_commit"]                      # the merge was clean
    assert candidate.clears_candidate_gate(db, cfg.ladder, mp, "compiles") is True
    assert candidate.clears_candidate_gate(db, cfg.ladder, mp, "tests") is False


def test_freeze_picks_largest_ready(gated):
    r, db, cfg = gated
    out = candidate.freeze(r.path, db, cfg)
    assert out is not None
    mp = db.get_merge_point(out["merge_point"])
    assert len(mp["member_patch_ids"]) == 2          # the pair, not a single
    assert gitio.read_ref(r.path, "refs/quilt/candidate/main") == out["commit"]
    assert db.active_candidate("main")["mp_id"] == mp["id"]


def test_freeze_refuses_second(gated):
    r, db, cfg = gated
    assert candidate.freeze(r.path, db, cfg) is not None
    assert candidate.freeze(r.path, db, cfg) is None


def test_advance_promotes_on_pass(gated):
    r, db, cfg = gated
    out = candidate.freeze(r.path, db, cfg)
    assert candidate.advance(r.path, db, cfg) is True
    assert gitio.read_ref(r.path, "refs/quilt/target/main") == out["commit"]
    mp = db.get_merge_point(out["merge_point"])
    assert mp["validation_state"] == "validated"
    assert db.gate_result(mp["id"], "t4day", mp["base_commit_sha"]) == "pass"
    assert db.active_candidate("main") is None       # promoted, not frozen


def test_advance_fail_marks_failed_and_queues(repo_with_branches, db, tmp_path):
    cfg = _cfg(tmp_path, CFG_PROMO.replace('final_cmd = "test -f base.txt"',
                                           'final_cmd = "false"'))
    scheduler.tick(repo_with_branches.path, db, cfg)
    out = candidate.freeze(repo_with_branches.path, db, cfg)
    assert candidate.advance(repo_with_branches.path, db, cfg) is False
    assert gitio.read_ref(repo_with_branches.path, "refs/quilt/target/main") is None
    assert db.active_candidate("main") is None       # failed, not frozen
    kinds = [w["kind"] for w in db.pending_work()]
    assert "test_fail" in kinds
    mp = db.get_merge_point(out["merge_point"])
    assert mp["validation_state"] == "untested"


def test_advance_without_freeze(gated):
    r, db, cfg = gated
    assert candidate.advance(r.path, db, cfg) is None


def test_advance_exception_resets_inflight(gated, monkeypatch):
    """A crash mid-advance must not leave the merge point stuck inflight."""
    r, db, cfg = gated
    out = candidate.freeze(r.path, db, cfg)

    def boom(*args, **kwargs):
        raise OSError("simulated crash")

    monkeypatch.setattr(candidate.subprocess, "run", boom)
    with pytest.raises(OSError):
        candidate.advance(r.path, db, cfg)
    mp = db.get_merge_point(out["merge_point"])
    assert mp["validation_state"] == "untested"
    assert db.active_candidate("main") is not None   # still frozen, retryable


def test_advance_refuses_poisoned_candidate(gated):
    """Poison set between freeze and advance must survive; candidate dies."""
    from quilt import resolve
    r, db, cfg = gated
    out = candidate.freeze(r.path, db, cfg)
    resolve.poison_merge_point(r.path, db, out["merge_point"])
    assert candidate.advance(r.path, db, cfg) is False
    mp = db.get_merge_point(out["merge_point"])
    assert mp["validation_state"] == "poison"        # never overwritten
    assert gitio.read_ref(r.path, "refs/quilt/target/main") is None
    assert db.active_candidate("main") is None       # failed, new freeze possible


def test_freeze_advance_cli(gated, tmp_path, capsys):
    from quilt import cli
    r, db, cfg = gated
    cfgfile = tmp_path / "promo.toml"          # written by _cfg already
    dbfile = tmp_path / "q.sqlite3"
    base = ["--repo", str(r.path), "--config", str(cfgfile), "--db", str(dbfile)]
    cli.main(base + ["freeze"])
    assert "frozen" in capsys.readouterr().out
    cli.main(base + ["advance"])
    assert "advanced" in capsys.readouterr().out
