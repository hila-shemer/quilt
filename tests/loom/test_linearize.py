import itertools

import pytest

from quilt import gates, gitio
from quilt.db import DB
from quilt.loom import increments, linearize, schema
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


def make_inc(repo, db, id, fname, content, prio="feature", at="main"):
    repo.git("checkout", "-q", "-b", f"b-{id}", at)
    sha = repo.commit_file(fname, content, f"inc {id}")
    repo.git("checkout", "-q", "main")
    inc = Increment(id=id, priority_class=prio, patches={"self": sha}, age=next(_age))
    increments.add(db, inc)
    return inc


def test_all_clean_full_series(repo, db, pool):
    a = make_inc(repo, db, "a", "a.txt", "A\n")
    b = make_inc(repo, db, "b", "b.txt", "B\n")
    sol = linearize.solve(repo.path, db, cfg(), [a, b], pool)
    assert sol.landed == ["a", "b"]
    assert sol.seam is None
    assert sol.staging_tip == sol.commit_of("b")
    assert gitio.read_ref(repo.path, linearize.STAGING_REF) == sol.commit_of("b")


def test_maximal_green_prefix_truncates(repo, db, pool):
    # gate fails iff bad.txt is present; b introduces it.
    c = cfg([{"name": "build", "test": False, "cmd": "! test -f bad.txt"}])
    a = make_inc(repo, db, "a", "a.txt", "A\n")
    b = make_inc(repo, db, "b", "bad.txt", "boom\n")
    cc = make_inc(repo, db, "c", "c.txt", "C\n")
    sol = linearize.solve(repo.path, db, c, [a, b, cc], pool)
    assert sol.landed == ["a"]
    assert sol.seam == "b" and sol.seam_kind == "test-fail"
    assert sol.staging_tip == sol.commit_of("a")
    assert increments.get(db, "b").status == "parked"
    assert increments.get(db, "c").status == "parked"   # everything past the seam


def test_test_only_increment_hoisted_to_front(repo, db, pool):
    feat = make_inc(repo, db, "feat", "feat.txt", "F\n", prio="feature")
    t = make_inc(repo, db, "t", "t.txt", "T\n", prio="test")
    sol = linearize.solve(repo.path, db, cfg(), [feat, t], pool)
    assert sol.order[0] == "t" and sol.landed[0] == "t"
    base = gitio.rev(repo.path, "main")
    assert gitio.rev(repo.path, sol.commit_of("t") + "^") == base   # lands directly on base


def test_conflict_seam_parks_and_enqueues(repo, db, pool):
    # a and b edit the same line of base.txt → cherry-picking b onto a conflicts.
    a = make_inc(repo, db, "a", "base.txt", "line1\nA\nline3\n")
    b = make_inc(repo, db, "b", "base.txt", "line1\nB\nline3\n")
    sol = linearize.solve(repo.path, db, cfg(), [a, b], pool)
    assert sol.landed == ["a"]
    assert sol.seam == "b" and sol.seam_kind == "conflict"
    assert increments.get(db, "b").status == "parked"
    assert db.pending_work()[0]["kind"] == "conflict"


def test_zero_llm_calls_on_clean_solve(repo, db, pool, tmp_path):
    # a configured audit_cmd that records if invoked; a clean solve must not call it.
    marker = tmp_path / "llm-called"
    from tests.conftest import make_stub
    stub = make_stub(tmp_path, "audit.sh",
                     f'#!/bin/sh\ntouch {marker}\ncat >/dev/null\necho \'{{"real_green":true}}\'\n')
    c = gates.Config(base="main", branches=[],
                     gates=[{"name": "build", "test": False, "cmd": "true"}],
                     targets={}, llm={"audit_cmd": str(stub)})
    a = make_inc(repo, db, "z", "z.txt", "Z\n")
    linearize.solve(repo.path, db, c, [a], pool)
    assert not marker.exists()
