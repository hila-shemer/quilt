import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from quilt.db import DB
from quilt.loom import increments, schema
from quilt.loom.increments import CycleError, Increment

# The tree under test: the subprocesses below must import *this* src/, not
# whatever an editable install points at (pyproject's `pythonpath = ["src"]`).
SRC = Path(__file__).resolve().parents[2] / "src"


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


def test_order_ties_break_by_id():
    """Increments tied on every policy component still come out in id order.

    The policy key alone cannot order these: all four are `(3, -0.0, 0, 0)`."""
    incs = [mk("c"), mk("a"), mk("d"), mk("b")]
    assert [i.id for i in increments.order(incs)] == ["a", "b", "c", "d"]


# --- the tie is resolved by set-iteration order, so it is hash-seeded --------
#
# `order()` builds `ready` from `remaining`, a `set` of str, and `min()` returns
# the *first* minimal element — so a tie is settled by set-iteration order, which
# is PYTHONHASHSEED-dependent. These two tests run the ordering in a subprocess
# under a spread of seeds, so the failure is a property of the code and not of
# the seed this pytest process happened to draw.

_TIE_SCRIPT = """
import json
from quilt.loom.increments import Increment, order
incs = [Increment(id=i, priority_class="feature", stability=0.0, size=0, age=0)
        for i in ("alpha-0-aaaaaaaa", "beta-0-bbbbbbbb",
                  "gamma-0-cccccccc", "delta-0-dddddddd")]
print(json.dumps([i.id for i in order(incs)]))
"""

# The live shape: pipeline.py seeds `age=i` from 0 *within each branch's*
# rev-list, so two branches each contributing an increment at index 0 tie on
# every component; milestone.py orders the whole green store in one call.
_LIVE_PATH_SCRIPT = """
import json
from quilt.loom.increments import Increment, order
incs = []
for br in ("topic-a", "topic-b"):
    for i, sha in enumerate(["aaaaaaaa", "bbbbbbbb"]):
        incs.append(Increment(id=f"{br}-{i}-{sha}", patches={"self": sha},
                              priority_class="feature", age=i))
print(json.dumps([i.id for i in order(incs)]))
"""


def _orders_across_hash_seeds(script, seeds=range(1, 9)):
    """Run `script` once per PYTHONHASHSEED, returning each stdout line."""
    env = dict(os.environ, PYTHONPATH=str(SRC))
    outs = []
    for seed in seeds:
        env["PYTHONHASHSEED"] = str(seed)
        r = subprocess.run([sys.executable, "-c", script], env=env,
                           capture_output=True, text=True, check=True)
        outs.append(r.stdout.strip())
    return outs


def test_order_ties_are_one_answer_across_hash_seeds():
    outs = _orders_across_hash_seeds(_TIE_SCRIPT)
    assert len(set(outs)) == 1, f"order() depends on PYTHONHASHSEED: {sorted(set(outs))}"
    assert json.loads(outs[0]) == ["alpha-0-aaaaaaaa", "beta-0-bbbbbbbb",
                                   "delta-0-dddddddd", "gamma-0-cccccccc"]


def test_order_live_path_ties_are_one_answer_across_hash_seeds():
    """The pipeline/milestone shape, where the answer decides which tips are
    absorbed: two branches tying at index 0 (and again at 1)."""
    outs = _orders_across_hash_seeds(_LIVE_PATH_SCRIPT)
    assert len(set(outs)) == 1, f"order() depends on PYTHONHASHSEED: {sorted(set(outs))}"
    assert json.loads(outs[0]) == ["topic-a-0-aaaaaaaa", "topic-b-0-aaaaaaaa",
                                   "topic-a-1-bbbbbbbb", "topic-b-1-bbbbbbbb"]


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
