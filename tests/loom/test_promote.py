"""P3 Task 2: FF promotion + milestone stress (spec §6.6)."""
import itertools

import pytest

from quilt import gates, gitio
from quilt.db import DB
from quilt.loom import increments, linearize, milestone, promote, schema
from quilt.loom.increments import Increment
from quilt.loom.worktree import WorktreePool

_age = itertools.count()


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "q.sqlite3")
    schema.apply(d.conn)
    return d


@pytest.fixture
def pool(repo, tmp_path):
    return WorktreePool(repo.path, root=tmp_path / "wt", size=4)


def cfg(stress_cmd="true", cand_cmd="true"):
    return gates.Config(
        base="main", branches=[],
        gates=[{"name": "build", "test": False, "cmd": cand_cmd}],
        targets={},
        promotion={"target": "next_staging",
                   "stress": {"name": "long", "test": False, "cmd": stress_cmd}})


def make_inc(repo, db, id, fname, content, prio="feature", at="main"):
    repo.git("checkout", "-q", "-b", f"b-{id}", at)
    sha = repo.commit_file(fname, content, f"inc {id}")
    repo.git("checkout", "-q", "main")
    inc = Increment(id=id, priority_class=prio, patches={"self": sha}, age=next(_age))
    increments.add(db, inc)
    return inc


def _count(p):
    return p.read_text().count("x") if p.exists() else 0


def test_validated_milestone_fast_forwards(repo, db, pool, tmp_path):
    cand_ctr = tmp_path / "cand"
    stress_ctr = tmp_path / "stress"
    c = cfg(stress_cmd=f"echo x >> {stress_ctr}; true",
            cand_cmd=f"echo x >> {cand_ctr}; true")
    a = make_inc(repo, db, "a", "a.txt", "A\n")
    b = make_inc(repo, db, "b", "b.txt", "B\n")
    linearize.solve(repo.path, db, c, [a, b], pool)
    cand_before = _count(cand_ctr)

    res = promote.run(repo.path, db, c, pool)

    first = milestone.milestones(repo.path, db, c)[0]
    assert res["promoted"] is True and res["milestone"] == first
    assert gitio.read_ref(repo.path, milestone.NEXT_STAGING_REF) == first
    # FF preserves commit identity → candidate gate NOT re-run (cache hot);
    # only the new stress gate is marginal work.
    assert _count(cand_ctr) == cand_before
    assert _count(stress_ctr) == 1


def test_stress_keeps_cache_hot_on_repromote(repo, db, pool, tmp_path):
    stress_ctr = tmp_path / "stress"
    c = cfg(stress_cmd=f"echo x >> {stress_ctr}; true")
    a = make_inc(repo, db, "a", "a.txt", "A\n")
    linearize.solve(repo.path, db, c, [a], pool)
    promote.run(repo.path, db, c, pool)
    # already at the only milestone; a second run finds nothing above the floor
    assert promote.run(repo.path, db, c, pool) is None
    assert _count(stress_ctr) == 1            # stress ran exactly once


def test_stress_fail_holds_floor(repo, db, pool):
    c = cfg(stress_cmd="false")
    a = make_inc(repo, db, "a", "a.txt", "A\n")
    b = make_inc(repo, db, "b", "b.txt", "B\n")
    linearize.solve(repo.path, db, c, [a, b], pool)
    staging_before = gitio.read_ref(repo.path, linearize.STAGING_REF)

    res = promote.run(repo.path, db, c, pool)

    assert res["promoted"] is False
    # floor never moved (next_staging was never created)
    assert gitio.read_ref(repo.path, milestone.NEXT_STAGING_REF) is None
    assert any(w["kind"] == "test_fail" for w in db.pending_work())
    # staging is free to run ahead — promote did not touch it
    assert gitio.read_ref(repo.path, linearize.STAGING_REF) == staging_before


def test_nothing_to_promote_returns_none(repo, db, pool):
    c = cfg()
    # no staging series solved → no milestones
    assert promote.run(repo.path, db, c, pool) is None


def test_ff_only_refuses_rewrite(repo, db):
    base = gitio.rev(repo.path, "main")
    repo.git("checkout", "-q", "-b", "x", base)
    cx = repo.commit_file("x.txt", "X\n")
    repo.git("checkout", "-q", "main")
    repo.git("checkout", "-q", "-b", "y", base)
    cy = repo.commit_file("y.txt", "Y\n")
    repo.git("checkout", "-q", "main")
    gitio.update_ref(repo.path, milestone.NEXT_STAGING_REF, cx)
    # cy does not descend from cx → a non-fast-forward; the floor must not rewrite.
    with pytest.raises(promote.NonFastForward):
        promote._fast_forward(repo.path, milestone.NEXT_STAGING_REF, cy)
    assert gitio.read_ref(repo.path, milestone.NEXT_STAGING_REF) == cx
