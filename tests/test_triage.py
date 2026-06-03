import pytest
from quilt import gates, llm, probe, triage
from quilt.db import DB
from tests.conftest import make_stub

STUB_MODERATE = ('#!/bin/sh\ncat >/dev/null\n'
                 'echo \'{"est_cause": "rename collision", "effort_class": "moderate"}\'\n')
STUB_COMPLEX = ('#!/bin/sh\ncat >/dev/null\n'
                'echo \'{"est_cause": "deep refactor", "effort_class": "complex"}\'\n')
STUB_GARBAGE = '#!/bin/sh\ncat >/dev/null\necho not json\n'


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "q.sqlite3")


def _cfg(tmp_path, stub_path):
    p = tmp_path / "quilt.toml"
    p.write_text(f"""
[quilt]
base = "main"
branches = ["feat-conflict"]

[llm]
triage_cmd = "{stub_path}"
""")
    return gates.load_config(p)


@pytest.fixture
def queued(repo_with_branches, db):
    """A repo with a genuine conflict already enqueued."""
    r = repo_with_branches
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")
    [res] = probe.probe_all(r.path, "main", ["feat-conflict"], db)
    assert res["construction"] == "conflict"
    db.enqueue_work("conflict", res["id"], "<<<<<<< markers")
    return r, db, res["id"]


def test_triage_routes_moderate(tmp_path, queued):
    r, db, mp_id = queued
    cfg = _cfg(tmp_path, make_stub(tmp_path, "t.sh", STUB_MODERATE))
    report = triage.drain(db, cfg)
    assert report == {"triaged": 1, "deferred": 0, "errors": 0}
    [item] = db.work_by_state("triaged", kind="conflict")
    assert item["target_id"] == mp_id
    row = db.get_triage(str(item["id"]))
    assert row["effort_class"] == "moderate"
    assert row["est_cause"] == "rename collision"


def test_triage_defers_complex(tmp_path, queued):
    r, db, mp_id = queued
    cfg = _cfg(tmp_path, make_stub(tmp_path, "t.sh", STUB_COMPLEX))
    report = triage.drain(db, cfg)
    assert report == {"triaged": 0, "deferred": 1, "errors": 0}
    assert db.work_by_state("deferred")[0]["target_id"] == mp_id


def test_triage_garbage_keeps_item_queued(tmp_path, queued):
    r, db, mp_id = queued
    cfg = _cfg(tmp_path, make_stub(tmp_path, "t.sh", STUB_GARBAGE))
    report = triage.drain(db, cfg)
    assert report["errors"] == 1
    assert len(db.pending_work()) == 1          # untouched


def test_triage_requires_config(queued, tmp_path):
    r, db, _ = queued
    p = tmp_path / "bare.toml"
    p.write_text('[quilt]\nbase = "main"\nbranches = ["feat-conflict"]\n')
    with pytest.raises(llm.LLMError):
        triage.drain(db, gates.load_config(p))


def test_triage_cli(tmp_path, queued, capsys):
    from quilt import cli
    r, db_unused, _ = queued
    stub = make_stub(tmp_path, "t.sh", STUB_MODERATE)
    cfgfile = tmp_path / "quilt.toml"
    cfgfile.write_text(f"""
[quilt]
base = "main"
branches = ["feat-conflict"]

[llm]
triage_cmd = "{stub}"
""")
    # CLI opens its own DB next to the config; re-enqueue there.
    from quilt.db import DB
    cli_db = DB(tmp_path / ".quilt.sqlite3")
    [res] = probe.probe_all(r.path, "main", ["feat-conflict"], cli_db)
    cli_db.enqueue_work("conflict", res["id"], "markers")
    cli.main(["--repo", str(r.path), "--config", str(cfgfile), "triage"])
    out = capsys.readouterr().out
    assert "triaged=1" in out
