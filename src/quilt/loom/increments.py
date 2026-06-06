"""Increment store + deterministic ordering (Loom spec §4.1, §4.2, §6.2).

The increment set is the source of truth; every integration branch is
`materialize(order(set, dep_edge, policy))`. `order()` is a topological sort
that honors the inferred `dep_edge` DAG (ground truth) and breaks ties by the
candidate-ordering policy.

Ordering policy (ascending sort key, every component ascending):
  1. priority_class:  test < invariant < fix < feature < rewrite   (tests first)
  2. stability:       higher score first  (more stable / less churn lands earlier)
  3. size:            smaller first       (larger increments land last)
  4. age:             older first         (lower insertion order earlier)

This pins the spec's `(priority_class, stability, -size, age)` shorthand:
"-stability" (higher first) and "+size" (smaller first), i.e. tests-first,
stable-first, small-first, old-first.
"""
import json
import time
from dataclasses import dataclass, field

PRIORITY_RANK = {"test": 0, "invariant": 1, "fix": 2, "feature": 3, "rewrite": 4}


class CycleError(Exception):
    """The dep_edge constraints contain a cycle; caller drops to commit-level
    interleave or emits split-needed (Loom §6.2, P2 Task 6)."""
    def __init__(self, remaining):
        self.remaining = remaining
        super().__init__(f"dependency cycle among: {sorted(remaining)}")


@dataclass
class Increment:
    id: str
    tier_target: str = "staging"
    patches: dict = field(default_factory=dict)
    priority_class: str = "feature"
    deps: list = field(default_factory=list)
    dod: dict | None = None
    base: str = ""
    oracle_ref: str | None = None
    status: str = "building"
    stability: float = 0.0
    size: int = 0
    patch_id: str = ""
    age: int = 0


def _key(inc: Increment):
    return (PRIORITY_RANK.get(inc.priority_class, 99), -inc.stability, inc.size, inc.age)


# --- CRUD -------------------------------------------------------------------

def add(db, inc: Increment) -> Increment:
    db.conn.execute(
        """INSERT INTO increment
             (id, tier_target, patches, priority_class, deps, dod, base,
              oracle_ref, status, stability, size, patch_id, age)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             tier_target=excluded.tier_target, patches=excluded.patches,
             priority_class=excluded.priority_class, deps=excluded.deps,
             dod=excluded.dod, base=excluded.base, oracle_ref=excluded.oracle_ref,
             status=excluded.status, stability=excluded.stability,
             size=excluded.size, patch_id=excluded.patch_id, age=excluded.age""",
        (inc.id, inc.tier_target, json.dumps(inc.patches), inc.priority_class,
         json.dumps(inc.deps), json.dumps(inc.dod) if inc.dod is not None else None,
         inc.base, inc.oracle_ref, inc.status, inc.stability, inc.size,
         inc.patch_id, inc.age))
    db.conn.commit()
    return inc


def _row_to_inc(row) -> Increment:
    d = dict(row)
    d["patches"] = json.loads(d["patches"])
    d["deps"] = json.loads(d["deps"])
    d["dod"] = json.loads(d["dod"]) if d["dod"] is not None else None
    return Increment(**d)


def get(db, inc_id: str) -> Increment | None:
    row = db.conn.execute("SELECT * FROM increment WHERE id=?", (inc_id,)).fetchone()
    return _row_to_inc(row) if row else None


def list_all(db, status: str | None = None) -> list[Increment]:
    if status:
        rows = db.conn.execute("SELECT * FROM increment WHERE status=?", (status,))
    else:
        rows = db.conn.execute("SELECT * FROM increment")
    return [_row_to_inc(r) for r in rows]


def set_status(db, inc_id: str, status: str) -> None:
    db.conn.execute("UPDATE increment SET status=? WHERE id=?", (status, inc_id))
    db.conn.commit()


# --- dep_edge ---------------------------------------------------------------

def add_dep_edge(db, x: str, y: str, evidence: str = "") -> None:
    """Record 'X red unless Y precedes' (Y is a prerequisite of X)."""
    db.conn.execute(
        """INSERT INTO dep_edge (x, y, evidence, created_at) VALUES (?,?,?,?)
           ON CONFLICT(x, y) DO UPDATE SET evidence=excluded.evidence""",
        (x, y, evidence, int(time.time())))
    db.conn.commit()


def list_dep_edges(db) -> list[dict]:
    return [dict(r) for r in db.conn.execute("SELECT * FROM dep_edge")]


# --- ordering ---------------------------------------------------------------

def order(incs: list[Increment], dep_edges: list | None = None) -> list[Increment]:
    """Topological sort honoring dep_edge (ground truth), tie-broken by the
    policy key. Raises CycleError if the constraints are cyclic.

    dep_edges may be a list of (x, y) tuples or of dict rows with 'x'/'y' keys.
    Edge (x, y) means y must precede x."""
    by_id = {inc.id: inc for inc in incs}
    ids = set(by_id)
    prereqs: dict[str, set] = {i: set() for i in ids}
    for e in (dep_edges or []):
        x, y = (e["x"], e["y"]) if isinstance(e, dict) else (e[0], e[1])
        if x in ids and y in ids:
            prereqs[x].add(y)

    placed: list[Increment] = []
    placed_ids: set = set()
    remaining = set(ids)
    while remaining:
        ready = [i for i in remaining if prereqs[i] <= placed_ids]
        if not ready:
            raise CycleError(remaining)
        nxt = min(ready, key=lambda i: _key(by_id[i]))
        placed.append(by_id[nxt])
        placed_ids.add(nxt)
        remaining.discard(nxt)
    return placed
