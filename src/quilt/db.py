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
  state      TEXT NOT NULL DEFAULT 'queued',  -- queued | done | dropped
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
        self.conn.execute("UPDATE merge_point SET validation_state=? WHERE id=?",
                          (state, mp_id))
        if state == "poison":
            poisoned = self.get_merge_point(mp_id)
            members = set(poisoned["member_patch_ids"])
            for mp in self.list_merge_points():
                if mp["id"] != mp_id and members < set(mp["member_patch_ids"]):
                    self.conn.execute(
                        "UPDATE merge_point SET validation_state='untested' WHERE id=?",
                        (mp["id"],))
        self.conn.commit()

    def enqueue_work(self, kind, target_id, detail=""):
        self.conn.execute(
            "INSERT INTO work_queue (kind, target_id, detail, created_at) VALUES (?,?,?,?)",
            (kind, target_id, detail, int(time.time())))
        self.conn.commit()

    def pending_work(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM work_queue WHERE state='queued' ORDER BY id")]
