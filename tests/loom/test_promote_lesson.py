"""P6 Task 4: cross-role lesson promotion + doctrine leak gate (spec §9.4)."""
import pytest

from quilt import gates
from quilt.db import DB
from quilt.loom import journal, promote_lesson, schema
from tests.conftest import make_stub


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "q.sqlite3")
    schema.apply(d.conn)
    return d


def cfg(llm=None):
    return gates.Config(base="main", branches=[], gates=[], targets={}, llm=llm or {})


# ---- cross-role promotion to project-global --------------------------------

def test_cross_role_lesson_promoted_to_global(db):
    a = journal.append(db, "resolver", "conflict", "abort + remerge",
                       kind="structured", pattern="header clash", recurrence=2)
    b = journal.append(db, "reviewer", "conflict", "abort + remerge",
                       kind="structured", pattern="header clash", recurrence=1)

    promoted = promote_lesson.promote_cross_role(db)

    assert len(promoted) == 1
    gl = journal.by_scope(db, "project-global")
    assert len(gl) == 1
    assert gl[0].pattern == "header clash"
    assert gl[0].recurrence == 3                      # summed across roles
    # superseded role-local instances evicted (roles share only via global)
    assert journal.get(db, a) is None and journal.get(db, b) is None


def test_single_role_recurrent_not_promoted(db):
    journal.append(db, "resolver", "conflict", "x", kind="structured",
                   pattern="solo", recurrence=9)
    journal.append(db, "resolver", "conflict", "x2", kind="structured",
                   pattern="solo", recurrence=9)

    assert promote_lesson.promote_cross_role(db) == []
    assert journal.by_scope(db, "project-global") == []
    assert len(journal.by_role(db, "resolver", "conflict")) == 2   # stays local


# ---- leak gate (HARD SAFETY GATE: stages, never pushes) --------------------

def test_leak_gate_stages_sanitized_candidate(db, tmp_path):
    stub = make_stub(tmp_path, "promoter.sh",
                     '#!/bin/sh\ncat >/dev/null\n'
                     'echo \'{"generic":"prefer an automated three-way merge before manual edits"}\'\n')
    c = cfg(llm={"promoter_cmd": str(stub)})
    gid = journal.append(db, "global", "conflict",
                         "in Majestic e2, the resolver prefers mediate",
                         scope="project-global")

    cand = promote_lesson.stage_doctrine(db, c, gid, tmp_path / "doctrine")

    assert cand.artifact.exists()
    assert "automated three-way merge" in cand.artifact.read_text()
    # tagged doctrine-upstream + staged locally — never pushed
    staged = journal.by_scope(db, "doctrine-upstream")
    assert len(staged) == 1 and "automated three-way merge" in staged[0].lesson


def test_leak_gate_rejects_residual_project_internal(db, tmp_path):
    # the model failed to sanitize — a Majestic name survives → fail closed, stage nothing.
    stub = make_stub(tmp_path, "promoter.sh",
                     '#!/bin/sh\ncat >/dev/null\n'
                     'echo \'{"generic":"this still references Majestic internals"}\'\n')
    c = cfg(llm={"promoter_cmd": str(stub)})
    gid = journal.append(db, "global", "conflict", "secret", scope="project-global")

    with pytest.raises(promote_lesson.LeakBlocked):
        promote_lesson.stage_doctrine(db, c, gid, tmp_path / "d")

    assert journal.by_scope(db, "doctrine-upstream") == []          # nothing staged
    assert not list((tmp_path / "d").glob("*")) if (tmp_path / "d").exists() else True


def test_leak_gate_blocks_without_promoter(db, tmp_path):
    gid = journal.append(db, "global", "conflict", "x", scope="project-global")
    with pytest.raises(promote_lesson.LeakBlocked):
        promote_lesson.stage_doctrine(db, cfg(), gid, tmp_path / "d")


def test_leak_gate_refuses_non_global_lesson(db, tmp_path):
    stub = make_stub(tmp_path, "promoter.sh",
                     '#!/bin/sh\ncat >/dev/null\necho \'{"generic":"ok"}\'\n')
    c = cfg(llm={"promoter_cmd": str(stub)})
    rid = journal.append(db, "resolver", "conflict", "local only")   # role-local
    with pytest.raises(ValueError):
        promote_lesson.stage_doctrine(db, c, rid, tmp_path / "d")
