"""role_journal store + scope tagging (Loom spec §4.4, §9.1).

The durable experience store the agent roles write to and retrieve from. Three
scopes (§9.1):
  - **role-local**       — `(role, task_type)`-keyed experience for this codebase.
  - **project-global**   — useful to all roles here; always loaded (small).
  - **doctrine-upstream**— generic, project-internal-free; the only scope that may
                           reach rightwayc (behind the §9.4 leak gate).

`recurrence` is the compaction/promotion signal ("hypothesis at 1, rule at N").
`refs` carries the files/symbols a lesson depends on, for dead-code decay (§9.3).
Retrieval (§9.2) lives in `retrieve.py`; compaction/promotion in `compact.py` /
`promote_lesson.py`.
"""
import json
import time
from dataclasses import dataclass, field

SCOPES = ("role-local", "project-global", "doctrine-upstream")
KINDS = ("structured", "narrative")


@dataclass
class Lesson:
    id: int
    role: str
    task_type: str
    kind: str
    pattern: str | None
    lesson: str
    recurrence: int
    refs: list = field(default_factory=list)
    scope: str = "role-local"
    created_at: int = 0


def _row_to_lesson(row) -> Lesson:
    d = dict(row)
    d["refs"] = json.loads(d["refs"])
    return Lesson(**d)


def append(db, role: str, task_type: str, lesson: str, *, kind: str = "narrative",
           pattern: str | None = None, refs=(), scope: str = "role-local",
           recurrence: int = 1) -> int:
    """Append a lesson; returns its row id."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope!r}")
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind!r}")
    cur = db.conn.execute(
        """INSERT INTO role_journal
             (role, task_type, kind, pattern, lesson, recurrence, refs, scope, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (role, task_type, kind, pattern, lesson, recurrence,
         json.dumps(list(refs)), scope, int(time.time())))
    db.conn.commit()
    return cur.lastrowid


def get(db, lesson_id: int) -> Lesson | None:
    row = db.conn.execute("SELECT * FROM role_journal WHERE id=?", (lesson_id,)).fetchone()
    return _row_to_lesson(row) if row else None


def by_role(db, role: str, task_type: str | None = None) -> list[Lesson]:
    if task_type is None:
        rows = db.conn.execute("SELECT * FROM role_journal WHERE role=? ORDER BY id",
                               (role,))
    else:
        rows = db.conn.execute(
            "SELECT * FROM role_journal WHERE role=? AND task_type=? ORDER BY id",
            (role, task_type))
    return [_row_to_lesson(r) for r in rows]


def by_scope(db, scope: str) -> list[Lesson]:
    rows = db.conn.execute("SELECT * FROM role_journal WHERE scope=? ORDER BY id", (scope,))
    return [_row_to_lesson(r) for r in rows]


def bump_recurrence(db, lesson_id: int, by: int = 1) -> None:
    db.conn.execute("UPDATE role_journal SET recurrence=recurrence+? WHERE id=?",
                    (by, lesson_id))
    db.conn.commit()


def delete(db, lesson_id: int) -> None:
    db.conn.execute("DELETE FROM role_journal WHERE id=?", (lesson_id,))
    db.conn.commit()


def set_scope(db, lesson_id: int, scope: str) -> None:
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope!r}")
    db.conn.execute("UPDATE role_journal SET scope=? WHERE id=?", (scope, lesson_id))
    db.conn.commit()
