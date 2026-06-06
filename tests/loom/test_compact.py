"""P6 Task 3: nightly journal compaction (spec §9.3)."""
import pytest

from quilt import gates
from quilt.db import DB
from quilt.loom import compact, journal, schema
from tests.conftest import make_stub


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "q.sqlite3")
    schema.apply(d.conn)
    return d


def cfg(llm=None):
    return gates.Config(base="main", branches=[], gates=[], targets={}, llm=llm or {})


# ---- deterministic dedup ---------------------------------------------------

def test_dedup_merges_same_pattern_and_sums_recurrence(db):
    a = journal.append(db, "r", "t", "abort + remerge", kind="structured",
                       pattern="header clash", recurrence=2)
    b = journal.append(db, "r", "t", "abort and remerge (restated)", kind="structured",
                       pattern="header clash", recurrence=3)
    merged = compact.dedup(db, "r", "t")
    assert merged == 1
    rows = journal.by_role(db, "r", "t")
    assert len(rows) == 1
    assert rows[0].id == a and rows[0].recurrence == 5      # earliest kept, summed
    assert journal.get(db, b) is None


def test_dedup_leaves_distinct_patterns_alone(db):
    journal.append(db, "r", "t", "one", kind="structured", pattern="alpha")
    journal.append(db, "r", "t", "two", kind="structured", pattern="beta")
    assert compact.dedup(db, "r", "t") == 0
    assert len(journal.by_role(db, "r", "t")) == 2


# ---- decay: evict lessons referencing dead code ----------------------------

def test_evicts_lesson_referencing_dead_code(repo, db):
    dead = journal.append(db, "r", "t", "about a deleted module", refs=["src/gone.py"])
    alive = journal.append(db, "r", "t", "about the base file", refs=["base.txt"])
    evicted = compact.evict_dead(db, repo.path)
    assert evicted == [dead]
    assert journal.get(db, dead) is None
    assert journal.get(db, alive) is not None              # live ref survives


def test_lesson_without_refs_is_never_evicted(repo, db):
    jid = journal.append(db, "r", "t", "no refs lesson")
    assert compact.evict_dead(db, repo.path) == []
    assert journal.get(db, jid) is not None


# ---- contradiction supersede (Haiku, stubbed) ------------------------------

def test_contradiction_supersedes_older(repo, db, tmp_path):
    stub = make_stub(tmp_path, "compactor.sh",
                     '#!/bin/sh\ncat >/dev/null\necho \'{"contradicts":true,"keep":"b"}\'\n')
    c = cfg(llm={"compactor_cmd": str(stub)})
    older = journal.append(db, "r", "t", "always rebase onto next")
    newer = journal.append(db, "r", "t", "never rebase; merge instead")
    removed = compact.supersede(db, c, "r", "t")
    assert removed == [older]
    assert journal.get(db, older) is None
    assert journal.get(db, newer) is not None


def test_no_compactor_cmd_means_no_supersede(db):
    journal.append(db, "r", "t", "lesson one")
    journal.append(db, "r", "t", "lesson two")
    assert compact.supersede(db, cfg(), "r", "t") == []   # no LLM configured → no-op
    assert len(journal.by_role(db, "r", "t")) == 2


def test_run_orchestrates_dedup_and_decay(repo, db):
    journal.append(db, "r", "t", "dup", kind="structured", pattern="p", recurrence=1)
    journal.append(db, "r", "t", "dup2", kind="structured", pattern="p", recurrence=1)
    journal.append(db, "r", "t", "dead", refs=["src/gone.py"])
    summary = compact.run(db, repo.path, cfg(), "r")
    assert summary["merged"] == 1 and summary["evicted"] == 1
