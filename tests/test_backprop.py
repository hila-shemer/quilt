import pytest
from quilt import agent, backprop, gates, gitio, probe
from quilt.db import DB
from tests.conftest import make_stub
from tests.test_agent import GLUE_STUB


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "q.sqlite3")


@pytest.fixture
def franken(repo_with_branches, db, tmp_path):
    """A frankenmerge with one glue commit, produced via the agent flow."""
    r = repo_with_branches
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")
    [res] = probe.probe_all(r.path, "main", ["feat-conflict"], db)
    db.enqueue_work("conflict", res["id"])
    item = db.pending_work()[0]
    stub = make_stub(tmp_path, "glue.sh", GLUE_STUB)
    cfgfile = tmp_path / "quilt.toml"
    cfgfile.write_text(f"""
[quilt]
base = "main"
branches = ["feat-conflict"]

[llm]
resolve_cmd = "{stub}"
""")
    cfg = gates.load_config(cfgfile)
    assert agent.resolve_conflict(r.path, db, cfg, dict(item))
    [fix] = db.list_fixes(state="pending")
    return r, db, cfg, fix


def test_offer_writes_patch_and_marks_offered(franken, tmp_path):
    r, db, cfg, fix = franken
    written = backprop.offer(r.path, db, tmp_path / "patches")
    assert len(written) == 1
    assert written[0].exists()
    assert "glue" in written[0].read_text()
    assert db.list_fixes(state="offered")[0]["id"] == fix["id"]
    assert db.list_fixes(state="pending") == []


def test_offer_idempotent(franken, tmp_path):
    r, db, cfg, fix = franken
    backprop.offer(r.path, db, tmp_path / "patches")
    assert backprop.offer(r.path, db, tmp_path / "patches") == []


def test_check_adopted_after_cherry_pick(franken, tmp_path):
    r, db, cfg, fix = franken
    backprop.offer(r.path, db, tmp_path / "patches")
    assert backprop.check_adopted(r.path, db, cfg) == []      # not yet adopted
    [glue_sha] = backprop._glue_commits(r.path, db, fix)
    r.git("checkout", "-q", "feat-conflict")
    r.git("cherry-pick", glue_sha)
    r.git("checkout", "-q", "main")
    assert backprop.check_adopted(r.path, db, cfg) == [fix["id"]]
    assert db.list_fixes(state="adopted")[0]["id"] == fix["id"]


def test_backprop_cli(franken, tmp_path, capsys):
    from quilt import cli
    r, db, cfg, fix = franken
    cli.main(["--repo", str(r.path), "--config", str(tmp_path / "quilt.toml"),
              "--db", str(tmp_path / "q.sqlite3"),
              "backprop", "--out", str(tmp_path / "patches")])
    out = capsys.readouterr().out
    assert "offered" in out
