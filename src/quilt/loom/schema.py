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


def apply(conn) -> None:
    conn.executescript(AUDIT_SCHEMA)
    conn.commit()
