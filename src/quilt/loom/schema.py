"""Additive Loom tables, applied to the same SQLite connection quilt.db uses.

`apply(conn)` is idempotent (CREATE TABLE IF NOT EXISTS) and composes with
quilt.db.SCHEMA without altering any existing quilt table. Each phase extends
this with the tables it introduces; P1 adds only `audit_result`.
"""

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_result (
  subject_id    TEXT NOT NULL,   -- merge-point id or commit sha being gated
  gate          TEXT NOT NULL,
  tree_sha      TEXT NOT NULL,   -- tree the binary was supposedly built from
  real_green    INTEGER NOT NULL,
  inconclusive  INTEGER NOT NULL,
  reason        TEXT NOT NULL,
  created_at    INTEGER NOT NULL,
  PRIMARY KEY (subject_id, gate, tree_sha)
);
"""

# P2 (linearizer): the increment set is the source of truth; dep_edge is the
# empirically-inferred DAG (ground truth, dominating the `deps` hint).
INCREMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS increment (
  id             TEXT PRIMARY KEY,
  tier_target    TEXT NOT NULL DEFAULT 'staging',  -- zoo | staging
  patches        TEXT NOT NULL DEFAULT '{}',       -- json {repo: patch_ref}
  priority_class TEXT NOT NULL DEFAULT 'feature',  -- test|invariant|fix|feature|rewrite
  deps           TEXT NOT NULL DEFAULT '[]',       -- json [increment_id]  (HINT only)
  dod            TEXT,                             -- json {required_test_sets}  (zoo)
  base           TEXT NOT NULL DEFAULT '',         -- next@<sha>
  oracle_ref     TEXT,
  status         TEXT NOT NULL DEFAULT 'building', -- green|red|building|parked
  stability      REAL NOT NULL DEFAULT 0,          -- stability score (higher = land earlier)
  size           INTEGER NOT NULL DEFAULT 0,       -- diff size (smaller = land earlier)
  patch_id       TEXT NOT NULL DEFAULT '',         -- git patch-id --stable (cache identity)
  age            INTEGER NOT NULL DEFAULT 0        -- insertion order (lower = older = earlier)
);
CREATE TABLE IF NOT EXISTS dep_edge (
  x          TEXT NOT NULL,   -- X red unless Y precedes  (=> Y is a prerequisite of X)
  y          TEXT NOT NULL,
  evidence   TEXT,
  witnessed  INTEGER NOT NULL DEFAULT 0,  -- coverage certified the edge (P4 §6.8)
  created_at INTEGER NOT NULL,
  PRIMARY KEY (x, y)
);
"""

# Per-commit gate cache (§4.3): keyed on the commit's TREE, distinct from quilt's
# per-merge-point gate_status. Identical trees at different commit shas share a
# row → fast-forward into next_staging is a pure cache-hit operation (P3).
COMMIT_GATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS commit_gate (
  tree_sha    TEXT NOT NULL,
  gate        TEXT NOT NULL,
  status      TEXT NOT NULL,   -- only 'pass' is cached; fails re-run
  result_ref  TEXT,
  finished_at INTEGER NOT NULL,
  PRIMARY KEY (tree_sha, gate)
);
"""


# Small key/value store for Loom engine metadata (reflow epoch, §10).
META_SCHEMA = """
CREATE TABLE IF NOT EXISTS loom_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def apply(conn) -> None:
    conn.executescript(AUDIT_SCHEMA)
    conn.executescript(INCREMENT_SCHEMA)
    conn.executescript(COMMIT_GATE_SCHEMA)
    conn.executescript(META_SCHEMA)
    conn.commit()
