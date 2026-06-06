"""P4 Task 2: coverage gate + seam-coverage certification (spec §6.8)."""
import pytest

from quilt import gates, gitio
from quilt.db import DB
from quilt.loom import coverage, increments, schema
from quilt.loom.worktree import WorktreePool


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "q.sqlite3")
    schema.apply(d.conn)
    return d


@pytest.fixture
def pool(repo, tmp_path):
    return WorktreePool(repo.path, root=tmp_path / "wt", size=2)


def cfg():
    return gates.Config(base="main", branches=[], gates=[], targets={})


def _witnessed(db, x, y):
    row = db.conn.execute("SELECT witnessed FROM dep_edge WHERE x=? AND y=?",
                          (x, y)).fetchone()
    return row["witnessed"]


# ---- report parsing + bar --------------------------------------------------

def test_parse_json_report():
    r = coverage.parse_report('{"percent": 91.5, "covered_paths": ["src/a.py"]}')
    assert r.percent == 91.5 and r.covered_paths == ["src/a.py"]


def test_parse_total_percent_text():
    r = coverage.parse_report("TOTAL      120     6    95%")
    assert r.percent == 95.0


def test_meets_bar():
    assert coverage.meets_bar(coverage.Report(90.0, []), 80.0)
    assert not coverage.meets_bar(coverage.Report(70.0, []), 80.0)


# ---- coverage gate as a ladder rung ----------------------------------------

def test_below_bar_fails_and_enqueues(repo, db, pool):
    g = {"name": "coverage", "bar": 80.0,
         "cmd": 'echo \'{"percent": 50.0, "covered_paths": []}\''}
    assert coverage.gate(repo.path, db, cfg(), "main", g, pool) == "fail"
    assert any(w["kind"] == "coverage_fail" for w in db.pending_work())


def test_at_or_above_bar_passes(repo, db, pool):
    g = {"name": "coverage", "bar": 80.0,
         "cmd": 'echo \'{"percent": 90.0, "covered_paths": []}\''}
    assert coverage.gate(repo.path, db, cfg(), "main", g, pool) == "pass"
    assert db.pending_work() == []


# ---- seam-coverage certification (validity input for P2's DAG) -------------

def test_certify_marks_edge_witnessed_when_path_executed(repo, db):
    increments.add_dep_edge(db, "x", "y", evidence="src/y.py")
    coverage.certify_edges(db, covered_paths=["src/y.py", "src/z.py"])
    assert _witnessed(db, "x", "y") == 1


def test_certify_flags_edge_unwitnessed_when_path_not_executed(repo, db):
    increments.add_dep_edge(db, "x", "y", evidence="src/y.py")
    coverage.certify_edges(db, covered_paths=["src/other.py"])
    assert _witnessed(db, "x", "y") == 0


def test_certify_edge_without_witness_paths_stays_unwitnessed(repo, db):
    # the seam classifier writes free-text evidence with no path → not trustable.
    increments.add_dep_edge(db, "x", "y", evidence="seam: hard-dep")
    coverage.certify_edges(db, covered_paths=["src/y.py"])
    assert _witnessed(db, "x", "y") == 0


def test_passing_gate_certifies_edges_from_its_report(repo, db, pool):
    increments.add_dep_edge(db, "x", "y", evidence="src/y.py")
    g = {"name": "coverage", "bar": 80.0,
         "cmd": 'echo \'{"percent": 95.0, "covered_paths": ["src/y.py"]}\''}
    assert coverage.gate(repo.path, db, cfg(), "main", g, pool) == "pass"
    assert _witnessed(db, "x", "y") == 1
