"""P6 Task 1: role_journal store + scope tagging (spec §4.4, §9.1)."""
import pytest

from quilt.db import DB
from quilt.loom import journal, schema


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "q.sqlite3")
    schema.apply(d.conn)
    return d


def test_append_narrative_defaults(db):
    jid = journal.append(db, "resolver", "conflict",
                         "prefer git-mediate before manual edits")
    assert isinstance(jid, int)
    rows = journal.by_role(db, "resolver", "conflict")
    assert len(rows) == 1
    l = rows[0]
    assert l.lesson == "prefer git-mediate before manual edits"
    assert l.kind == "narrative"
    assert l.recurrence == 1                 # hypothesis at 1, rule at N
    assert l.scope == "role-local"           # default scope
    assert l.refs == []


def test_append_structured_with_pattern_and_refs(db):
    jid = journal.append(db, "resolver", "conflict", "abort + re-merge from base",
                         kind="structured", pattern="same-line edit in shared header",
                         refs=["src/a.h", "src/a.c"], scope="project-global")
    l = journal.get(db, jid)
    assert l.kind == "structured"
    assert l.pattern == "same-line edit in shared header"
    assert l.refs == ["src/a.h", "src/a.c"]
    assert l.scope == "project-global"


def test_by_role_without_task_type_returns_all_for_role(db):
    journal.append(db, "resolver", "conflict", "x")
    journal.append(db, "resolver", "fix-review", "y")
    journal.append(db, "debugger", "conflict", "z")
    assert len(journal.by_role(db, "resolver", "conflict")) == 1
    assert len(journal.by_role(db, "resolver")) == 2
    assert len(journal.by_role(db, "debugger")) == 1


def test_bump_recurrence(db):
    jid = journal.append(db, "r", "t", "l")
    journal.bump_recurrence(db, jid)
    assert journal.get(db, jid).recurrence == 2
    journal.bump_recurrence(db, jid, by=3)
    assert journal.get(db, jid).recurrence == 5


def test_by_scope(db):
    journal.append(db, "r", "t", "local one")
    journal.append(db, "r", "t", "global one", scope="project-global")
    journal.append(db, "r2", "t", "another global", scope="project-global")
    assert len(journal.by_scope(db, "project-global")) == 2
    assert len(journal.by_scope(db, "role-local")) == 1
