"""SQLite store. The DB is the source of truth about combinations; git only
stores trees and refs. Both member_patch_ids and member_tips are stored as
JSON-encoded sorted lists."""
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS merge_point (
  id               TEXT PRIMARY KEY,
  base_tree_sha    TEXT NOT NULL,
  base_commit_sha  TEXT NOT NULL,
  member_patch_ids TEXT NOT NULL,
  member_tips      TEXT NOT NULL,
  result_commit    TEXT,
  result_tree      TEXT,
  construction     TEXT NOT NULL,
  validation_state TEXT NOT NULL DEFAULT 'untested',
  created_at       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS gate_status (
  merge_point_id  TEXT NOT NULL REFERENCES merge_point(id),
  gate            TEXT NOT NULL,
  base_commit_sha TEXT NOT NULL,
  status          TEXT NOT NULL,
  result_ref      TEXT,
  started_at      INTEGER,
  finished_at     INTEGER,
  PRIMARY KEY (merge_point_id, gate, base_commit_sha)
);
CREATE TABLE IF NOT EXISTS frankenmerge_fix (
  merge_point_id TEXT NOT NULL REFERENCES merge_point(id),
  patch_ref      TEXT NOT NULL,
  affected_tips  TEXT NOT NULL,
  backprop_state TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS triage (
  id           TEXT PRIMARY KEY,
  target_id    TEXT NOT NULL,
  kind         TEXT NOT NULL,
  est_cause    TEXT,
  effort_class TEXT NOT NULL,
  model        TEXT,
  created_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS work_queue (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT NOT NULL,         -- conflict | test_fail
  target_id  TEXT NOT NULL,
  detail     TEXT,
  state      TEXT NOT NULL DEFAULT 'queued',  -- queued | triaged | deferred | done | dropped
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  target     TEXT NOT NULL,
  mp_id      TEXT NOT NULL REFERENCES merge_point(id),
  commit_sha TEXT NOT NULL,
  state      TEXT NOT NULL DEFAULT 'frozen',  -- frozen | promoted | failed
  created_at INTEGER NOT NULL
);
"""


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def upsert_merge_point(self, *, id, base_tree_sha, base_commit_sha,
                           member_patch_ids, member_tips, construction,
                           result_commit=None, result_tree=None):
        self.conn.execute(
            """INSERT INTO merge_point (id, base_tree_sha, base_commit_sha,
                 member_patch_ids, member_tips, result_commit, result_tree,
                 construction, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 base_commit_sha=excluded.base_commit_sha,
                 member_tips=excluded.member_tips,
                 result_commit=excluded.result_commit,
                 result_tree=excluded.result_tree,
                 construction=excluded.construction""",
            (id, base_tree_sha, base_commit_sha,
             json.dumps(sorted(member_patch_ids)), json.dumps(member_tips),
             result_commit, result_tree, construction, int(time.time())))
        self.conn.commit()

    def get_merge_point(self, id):
        row = self.conn.execute("SELECT * FROM merge_point WHERE id=?", (id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["member_patch_ids"] = json.loads(d["member_patch_ids"])
        d["member_tips"] = json.loads(d["member_tips"])
        return d

    def list_merge_points(self, base_tree_sha=None):
        q, args = "SELECT id FROM merge_point", ()
        if base_tree_sha:
            q += " WHERE base_tree_sha=?"
            args = (base_tree_sha,)
        return [self.get_merge_point(r["id"]) for r in self.conn.execute(q, args)]

    def record_gate(self, mp_id, gate, base_sha, status, result_ref=None):
        now = int(time.time())
        self.conn.execute(
            """INSERT INTO gate_status VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(merge_point_id, gate, base_commit_sha)
               DO UPDATE SET status=excluded.status, result_ref=excluded.result_ref,
                             finished_at=excluded.finished_at""",
            (mp_id, gate, base_sha, status, result_ref, now, now))
        self.conn.commit()

    def gate_result(self, mp_id, gate, base_sha):
        row = self.conn.execute(
            "SELECT status FROM gate_status WHERE merge_point_id=? AND gate=? AND base_commit_sha=?",
            (mp_id, gate, base_sha)).fetchone()
        return row["status"] if row else None

    def highest_gate(self, mp_id, base_sha, ladder):
        highest = None
        for gate in ladder:
            if self.gate_result(mp_id, gate, base_sha) != "pass":
                break
            highest = gate
        return highest

    def set_validation(self, mp_id, state):
        """Set validation state. Returns list of cascade-reset merge-point ids
        (supersets reset to 'untested') when state=='poison', else []."""
        self.conn.execute("UPDATE merge_point SET validation_state=? WHERE id=?",
                          (state, mp_id))
        cascade_ids = []
        if state == "poison":
            poisoned = self.get_merge_point(mp_id)
            members = set(poisoned["member_patch_ids"])
            for mp in self.list_merge_points():
                if mp["id"] != mp_id and members < set(mp["member_patch_ids"]):
                    self.conn.execute(
                        "UPDATE merge_point SET validation_state='untested' WHERE id=?",
                        (mp["id"],))
                    cascade_ids.append(mp["id"])
        self.conn.commit()
        return cascade_ids

    def enqueue_work(self, kind, target_id, detail=""):
        existing = self.conn.execute(
            "SELECT 1 FROM work_queue WHERE kind=? AND target_id=? AND state='queued'",
            (kind, target_id)).fetchone()
        if existing:
            return
        self.conn.execute(
            "INSERT INTO work_queue (kind, target_id, detail, created_at) VALUES (?,?,?,?)",
            (kind, target_id, detail, int(time.time())))
        self.conn.commit()

    def find_merge_point(self, prefix: str) -> list[str]:
        """Return list of full IDs matching prefix (SQL LIKE 'prefix%')."""
        rows = self.conn.execute(
            "SELECT id FROM merge_point WHERE id LIKE ?", (prefix + "%",)
        ).fetchall()
        return [r["id"] for r in rows]

    def pending_work(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM work_queue WHERE state='queued' ORDER BY id")]

    def set_work_state(self, work_id, state):
        self.conn.execute("UPDATE work_queue SET state=? WHERE id=?",
                          (state, work_id))
        self.conn.commit()

    def work_by_state(self, state, kind=None):
        q, args = "SELECT * FROM work_queue WHERE state=?", [state]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY id", args)]

    def record_triage(self, id, target_id, kind, est_cause, effort_class,
                      model=None):
        self.conn.execute(
            """INSERT INTO triage (id, target_id, kind, est_cause, effort_class,
                 model, created_at) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET est_cause=excluded.est_cause,
                 effort_class=excluded.effort_class, model=excluded.model""",
            (id, target_id, kind, est_cause, effort_class, model,
             int(time.time())))
        self.conn.commit()

    def get_triage(self, id):
        row = self.conn.execute("SELECT * FROM triage WHERE id=?",
                                (id,)).fetchone()
        return dict(row) if row else None

    def add_fix(self, mp_id, patch_ref, affected_tips):
        cur = self.conn.execute(
            """INSERT INTO frankenmerge_fix
                 (merge_point_id, patch_ref, affected_tips, backprop_state)
               VALUES (?,?,?,'pending')""",
            (mp_id, patch_ref, json.dumps(affected_tips)))
        self.conn.commit()
        return cur.lastrowid

    def list_fixes(self, state=None):
        q, args = "SELECT rowid AS id, * FROM frankenmerge_fix", ()
        if state:
            q += " WHERE backprop_state=?"
            args = (state,)
        out = []
        for r in self.conn.execute(q, args):
            d = dict(r)
            d["affected_tips"] = json.loads(d["affected_tips"])
            out.append(d)
        return out

    def set_fix_state(self, fix_id, state):
        self.conn.execute(
            "UPDATE frankenmerge_fix SET backprop_state=? WHERE rowid=?",
            (state, fix_id))
        self.conn.commit()

    def add_candidate(self, target, mp_id, commit_sha):
        cur = self.conn.execute(
            """INSERT INTO candidate (target, mp_id, commit_sha, created_at)
               VALUES (?,?,?,?)""",
            (target, mp_id, commit_sha, int(time.time())))
        self.conn.commit()
        return cur.lastrowid

    def active_candidate(self, target):
        row = self.conn.execute(
            """SELECT * FROM candidate WHERE target=? AND state='frozen'
               ORDER BY id DESC LIMIT 1""", (target,)).fetchone()
        return dict(row) if row else None

    def set_candidate_state(self, cand_id, state):
        self.conn.execute("UPDATE candidate SET state=? WHERE id=?",
                          (state, cand_id))
        self.conn.commit()
