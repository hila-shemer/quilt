"""P3 Task 1: milestone selection + frozen floor (spec §3, §6.6)."""
import itertools

import pytest

from quilt import gates, gitio
from quilt.db import DB
from quilt.loom import increments, linearize, milestone, schema
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


def cfg(gate_dicts=None):
    gate_dicts = gate_dicts or [{"name": "build", "test": False, "cmd": "true"}]
    return gates.Config(base="main", branches=[], gates=gate_dicts, targets={})


def make_inc(repo, db, id, fname, content, prio="feature", at="main", base=""):
    repo.git("checkout", "-q", "-b", f"b-{id}", at)
    sha = repo.commit_file(fname, content, f"inc {id}")
    repo.git("checkout", "-q", "main")
    inc = Increment(id=id, priority_class=prio, patches={"self": sha},
                    age=next(_age), base=base)
    increments.add(db, inc)
    return inc


def test_milestones_are_per_increment_tips_in_order(repo, db, pool):
    a = make_inc(repo, db, "a", "a.txt", "A\n")
    b = make_inc(repo, db, "b", "b.txt", "B\n")
    c = make_inc(repo, db, "c", "c.txt", "C\n")
    sol = linearize.solve(repo.path, db, cfg(), [a, b, c], pool)
    assert sol.landed == ["a", "b", "c"]
    ms = milestone.milestones(repo.path, db, cfg())
    assert ms == [sol.commit_of("a"), sol.commit_of("b"), sol.commit_of("c")]


def test_mutable_suffix_excludes_frozen_floor(repo, db, pool):
    a = make_inc(repo, db, "a", "a.txt", "A\n")
    b = make_inc(repo, db, "b", "b.txt", "B\n")
    c = make_inc(repo, db, "c", "c.txt", "C\n")
    sol = linearize.solve(repo.path, db, cfg(), [a, b, c], pool)
    # next_staging floor sits at A → only B,C are mutable
    gitio.update_ref(repo.path, "refs/loom/next_staging", sol.commit_of("a"))
    suffix = milestone.mutable_suffix(repo.path, "refs/loom/next_staging")
    assert suffix == [sol.commit_of("b"), sol.commit_of("c")]


def test_mutable_suffix_is_full_series_without_floor(repo, db, pool):
    a = make_inc(repo, db, "a", "a.txt", "A\n")
    b = make_inc(repo, db, "b", "b.txt", "B\n")
    sol = linearize.solve(repo.path, db, cfg(), [a, b], pool)
    # no next_staging ref yet → floor is base, whole series is mutable
    suffix = milestone.mutable_suffix(repo.path, "refs/loom/next_staging",
                                      base=gitio.rev(repo.path, "main"))
    assert suffix == [sol.commit_of("a"), sol.commit_of("b")]


def test_parked_increment_past_seam_is_not_a_milestone(repo, db, pool):
    # milestones come from the increment store's GREEN set only: a parked
    # increment past a red seam never becomes a milestone.
    c = cfg([{"name": "build", "test": False, "cmd": "! test -f bad.txt"}])
    a = make_inc(repo, db, "a", "a.txt", "A\n")
    b = make_inc(repo, db, "b", "bad.txt", "boom\n")     # seam
    cc = make_inc(repo, db, "c", "c.txt", "C\n")
    sol = linearize.solve(repo.path, db, c, [a, b, cc], pool)
    assert sol.landed == ["a"] and increments.get(db, "c").status == "parked"
    ms = milestone.milestones(repo.path, db, c)
    assert ms == [sol.commit_of("a")]
