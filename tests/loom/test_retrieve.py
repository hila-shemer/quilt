"""P6 Task 2: relevance retrieval for decision context (spec §9.2, §7)."""
import pytest

from quilt.db import DB
from quilt.loom import decide, journal, retrieve, schema


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "q.sqlite3")
    schema.apply(d.conn)
    return d


def test_globals_always_included_locals_never_wholesale(db):
    journal.append(db, "resolver", "conflict", "GLOBAL ALPHA", scope="project-global")
    journal.append(db, "resolver", "conflict", "GLOBAL BETA", scope="project-global")
    # one relevant local, one irrelevant local
    journal.append(db, "resolver", "conflict", "abort and remerge header clash",
                     kind="structured", pattern="header conflict shared include")
    journal.append(db, "resolver", "conflict", "IRRELEVANT lesson about timezones",
                     kind="structured", pattern="datetime parsing offset")

    ctx = retrieve.context_for(db, "resolver", "conflict",
                               signal="conflict in a shared header include")

    assert "GLOBAL ALPHA" in ctx and "GLOBAL BETA" in ctx     # all globals
    assert "header clash" in ctx                              # relevant local in
    assert "timezones" not in ctx                             # irrelevant local out


def test_irrelevant_signal_yields_only_globals(db):
    journal.append(db, "resolver", "conflict", "GLOBAL ONLY", scope="project-global")
    journal.append(db, "resolver", "conflict", "local about parsers",
                     kind="structured", pattern="parser lookahead table")
    ctx = retrieve.context_for(db, "resolver", "conflict",
                               signal="completely unrelated words xyzzy")
    assert "GLOBAL ONLY" in ctx
    assert "parsers" not in ctx


def test_local_ranking_prefers_higher_overlap_and_recurrence(db):
    journal.append(db, "r", "t", "low overlap", kind="structured",
                   pattern="alpha")
    journal.append(db, "r", "t", "high overlap one", kind="structured",
                   pattern="alpha beta gamma")
    ctx = retrieve.context_for(db, "r", "t", signal="alpha beta gamma delta",
                               budget_words=8)
    # budget admits only the best-matching local (7 words), not the second (4 more)
    assert "high overlap one" in ctx
    assert "low overlap" not in ctx


def test_budget_caps_number_of_locals(db):
    for i in range(10):
        journal.append(db, "r", "t", f"lesson number {i} match", kind="structured",
                       pattern="match token")
    ctx = retrieve.context_for(db, "r", "t", signal="match token",
                               budget_words=8)
    picked = [ln for ln in ctx.splitlines() if ln.startswith("- ")]
    assert 0 < len(picked) < 10           # truncated, not wholesale


def test_files_contribute_to_relevance(db):
    journal.append(db, "r", "t", "touches the parser", kind="structured",
                   pattern="x", refs=["src/parser.c"])
    ctx = retrieve.context_for(db, "r", "t", files=["src/parser.c"], signal="")
    assert "touches the parser" in ctx


# ---- wiring: decide.context_for now returns real context (closes P2 stub) ---

def test_decide_context_for_delegates_to_retrieve(db):
    journal.append(db, "seam", "seam", "ALWAYS CHECK LIB DEPS", scope="project-global")
    ctx = decide.context_for(db, "seam", "seam")
    assert "ALWAYS CHECK LIB DEPS" in ctx


def test_decide_context_for_none_db_is_empty(db):
    assert decide.context_for(None, "seam", "seam") == ""
