import pytest

from quilt.db import DB
from quilt.loom import increments, schema
from quilt.loom.increments import CycleError, Increment


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "q.sqlite3")
    schema.apply(d.conn)
    return d


def mk(id, prio="feature", stability=0.0, size=0, age=0):
    return Increment(id=id, priority_class=prio, stability=stability, size=size, age=age)


def test_order_puts_tests_first():
    out = increments.order([mk("f", "feature"), mk("t", "test")])
    assert out[0].id == "t"


def test_order_secondary_keys_total_and_deterministic():
    incs = [
        mk("a", "feature", stability=1.0, size=10, age=2),
        mk("b", "feature", stability=2.0, size=10, age=1),   # higher stability → earlier
        mk("c", "feature", stability=2.0, size=5, age=3),    # same stab, smaller → earlier
        mk("d", "feature", stability=2.0, size=5, age=0),    # same, older → earlier
    ]
    out = [i.id for i in increments.order(incs)]
    assert out == ["d", "c", "b", "a"]
    # deterministic: same input → same output
    assert out == [i.id for i in increments.order(list(reversed(incs)))]


def test_dep_edge_overrides_key():
    # x is a 'test' (would sort first by key) but edge (x->y) forces y before x.
    x = mk("x", "test")
    y = mk("y", "feature")
    out = [i.id for i in increments.order([x, y], [("x", "y")])]
    assert out.index("y") < out.index("x")


def test_cycle_raises():
    a, b = mk("a"), mk("b")
    with pytest.raises(CycleError):
        increments.order([a, b], [("a", "b"), ("b", "a")])


def test_crud_roundtrip(db):
    inc = Increment(id="i1", tier_target="zoo", patches={"self": "ref/x"},
                    priority_class="fix", deps=["i0"], dod={"sets": ["unit"]},
                    base="next@abc", status="building", stability=1.5, size=7,
                    patch_id="pid1", age=3)
    increments.add(db, inc)
    got = increments.get(db, "i1")
    assert got.patches == {"self": "ref/x"}
    assert got.dod == {"sets": ["unit"]}
    assert got.priority_class == "fix" and got.size == 7
    increments.set_status(db, "i1", "green")
    assert increments.get(db, "i1").status == "green"


def test_dep_edge_crud(db):
    increments.add_dep_edge(db, "x", "y", evidence="seam diff")
    edges = increments.list_dep_edges(db)
    assert {(e["x"], e["y"]) for e in edges} == {("x", "y")}
