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


# ---- Task 5: seam classification + reorder-before-repair -------------------

from tests.conftest import make_stub

_NEEDS_LIB = [{"name": "build", "test": False, "cmd": "test -f lib.txt"}]


def _cfg_seam(tmp_path, verdict_json, gate_dicts=None):
    stub = make_stub(tmp_path, "seam.sh",
                     f'#!/bin/sh\ncat >/dev/null\necho \'{verdict_json}\'\n')
    return gates.Config(base="main", branches=[],
                        gates=gate_dicts or _NEEDS_LIB, targets={},
                        llm={"seam_cmd": str(stub)})


def test_hard_dep_reorders_and_records_edge(repo, db, pool, tmp_path):
    c = _cfg_seam(tmp_path, '{"kind":"hard","easy_fix":false}')
    x = make_inc(repo, db, "x", "x.txt", "X\n")        # needs lib.txt (created first)
    y = make_inc(repo, db, "y", "lib.txt", "ok\n")     # provides lib.txt
    sol = linearize.solve_seams(repo.path, db, c, [x, y], pool)
    assert sol.seam is None
    assert sol.landed == ["y", "x"]                     # reordered: prerequisite first
    assert sol.order.index("y") < sol.order.index("x")
    assert ("x", "y") in {(e["x"], e["y"]) for e in increments.list_dep_edges(db)}


def test_incidental_does_not_reorder_and_parks(repo, db, pool, tmp_path):
    c = _cfg_seam(tmp_path, '{"kind":"incidental","easy_fix":true}')
    x = make_inc(repo, db, "x", "x.txt", "X\n")        # red: lib.txt never provided in this order
    y = make_inc(repo, db, "y", "lib.txt", "ok\n")
    sol = linearize.solve_seams(repo.path, db, c, [x, y], pool)
    assert sol.seam == "x"
    assert increments.get(db, "x").status == "parked"
    assert increments.list_dep_edges(db) == []          # no reorder attempted
    assert any(w["kind"] == "test_fail" for w in db.pending_work())


def test_no_classifier_defaults_to_repair(repo, db, pool):
    # no seam_cmd configured → safe default is incidental (park), not reorder.
    c = gates.Config(base="main", branches=[], gates=_NEEDS_LIB, targets={})
    x = make_inc(repo, db, "x", "x.txt", "X\n")
    y = make_inc(repo, db, "y", "lib.txt", "ok\n")
    sol = linearize.solve_seams(repo.path, db, c, [x, y], pool)
    assert sol.seam == "x" and increments.list_dep_edges(db) == []


# ---- Task 6: cycle handling — interleave then split-needed -----------------

def test_topo_commits_orders_acyclic():
    order = linearize._topo_commits(["a", "b", "c"], {"b": {"a"}, "c": {"b"}})
    assert order == ["a", "b", "c"]


def test_topo_commits_detects_cycle():
    assert linearize._topo_commits(["a", "b"], {"a": {"b"}, "b": {"a"}}) is None


def test_irreducible_cycle_emits_split_needed(repo, db, pool):
    x = make_inc(repo, db, "x", "x.txt", "X\n")
    y = make_inc(repo, db, "y", "y.txt", "Y\n")
    increments.add_dep_edge(db, "x", "y")
    increments.add_dep_edge(db, "y", "x")          # series-level cycle
    sol = linearize.solve_seams(repo.path, db, cfg(), [x, y], pool)
    assert set(sol.split_needed) == {"x", "y"}
    assert increments.get(db, "x").status == "parked"
    assert increments.get(db, "y").status == "parked"
    assert any(w["kind"] == "split_needed" for w in db.pending_work())


def test_commit_interleave_irreducible_for_single_commit_cycle(repo, db):
    x = make_inc(repo, db, "x", "x.txt", "X\n")
    y = make_inc(repo, db, "y", "y.txt", "Y\n")
    edges = [{"x": "x", "y": "y"}, {"x": "y", "y": "x"}]
    assert linearize._commit_interleave(repo.path, [x, y], {"x", "y"}, edges) is None


def test_solve_commits_materializes_in_order(repo, db, pool):
    # the reducible-path materializer, exercised directly on a valid acyclic order
    a = make_inc(repo, db, "a", "a.txt", "A\n")
    b = make_inc(repo, db, "b", "b.txt", "B\n")
    sol = linearize._solve_commits(repo.path, db, cfg(),
                                   [a.patches["self"], b.patches["self"]], pool)
    assert sol.seam is None and len(sol.landed) == 2
    assert gitio.read_ref(repo.path, linearize.STAGING_REF) == sol.staging_tip


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
