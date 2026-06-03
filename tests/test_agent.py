import pytest
from quilt import agent, gates, gitio, probe
from quilt.db import DB
from tests.conftest import make_stub

# Deterministic "agent": delete conflict markers, keeping both sides.
RESOLVE_STUB = """#!/bin/sh
cat >/dev/null
for f in $(git diff --name-only --diff-filter=U); do
  sed -i -e '/^<<<<<<</d' -e '/^=======$/d' -e '/^>>>>>>>/d' "$f"
done
"""

# Same, but concludes the merge itself and adds a glue commit on top.
GLUE_STUB = RESOLVE_STUB + """git add -A
git commit -qm "agent: resolve merge"
echo glue > glue.txt
git add glue.txt
git commit -qm "agent: glue fix"
"""

FAIL_STUB = "#!/bin/sh\ncat >/dev/null\nexit 1\n"


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "q.sqlite3")


def _cfg(tmp_path, stub_path):
    p = tmp_path / "quilt.toml"
    p.write_text(f"""
[quilt]
base = "main"
branches = ["feat-conflict"]

[[gate]]
name = "compiles"
cmd = "test -f base.txt"

[targets]
next = "compiles"

[llm]
resolve_cmd = "{stub_path}"
""")
    return gates.load_config(p)


@pytest.fixture
def conflict_item(repo_with_branches, db):
    r = repo_with_branches
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")
    [res] = probe.probe_all(r.path, "main", ["feat-conflict"], db)
    assert res["construction"] == "conflict"
    db.enqueue_work("conflict", res["id"], "conflict in base.txt")
    item = db.pending_work()[0]
    db.set_work_state(item["id"], "triaged")
    return r, db, dict(item, state="triaged")


def test_agent_resolves_and_pins(tmp_path, conflict_item):
    r, db, item = conflict_item
    cfg = _cfg(tmp_path, make_stub(tmp_path, "a.sh", RESOLVE_STUB))
    assert agent.resolve_conflict(r.path, db, cfg, item) is True
    mp = db.get_merge_point(item["target_id"])
    assert mp["construction"] == "agent"
    assert mp["result_commit"]
    assert gitio.read_ref(r.path, f"refs/quilt/{mp['id']}") == mp["result_commit"]
    assert db.work_by_state("done")[0]["id"] == item["id"]
    assert db.list_fixes() == []
    # merged content kept both sides
    merged = r.git("show", f"{mp['result_commit']}:base.txt")
    assert "MAIN" in merged and "CONFLICT" in merged


def test_glue_commits_make_frankenmerge(tmp_path, conflict_item):
    r, db, item = conflict_item
    cfg = _cfg(tmp_path, make_stub(tmp_path, "a.sh", GLUE_STUB))
    assert agent.resolve_conflict(r.path, db, cfg, item) is True
    mp = db.get_merge_point(item["target_id"])
    assert mp["construction"] == "frankenmerge"
    [fix] = db.list_fixes(state="pending")
    assert fix["merge_point_id"] == mp["id"]
    assert gitio.read_ref(r.path, fix["patch_ref"]) == mp["result_commit"]
    assert r.git("show", f"{mp['result_commit']}:glue.txt").strip() == "glue"


def test_failed_agent_leaves_item(tmp_path, conflict_item):
    r, db, item = conflict_item
    cfg = _cfg(tmp_path, make_stub(tmp_path, "a.sh", FAIL_STUB))
    assert agent.resolve_conflict(r.path, db, cfg, item) is False
    assert db.get_merge_point(item["target_id"])["construction"] == "conflict"
    assert db.work_by_state("triaged")[0]["id"] == item["id"]


def test_resolved_merge_point_gates_on_next_tick(tmp_path, conflict_item):
    from quilt import scheduler
    r, db, item = conflict_item
    cfg = _cfg(tmp_path, make_stub(tmp_path, "a.sh", RESOLVE_STUB))
    assert agent.resolve_conflict(r.path, db, cfg, item) is True
    report = scheduler.tick(r.path, db, cfg)
    mp = db.get_merge_point(item["target_id"])
    assert db.gate_result(mp["id"], "compiles", mp["base_commit_sha"]) == "pass"


def test_resolve_cli(tmp_path, conflict_item, capsys):
    from quilt import cli
    r, db, item = conflict_item
    stub = make_stub(tmp_path, "a.sh", RESOLVE_STUB)
    cfgfile = tmp_path / "c.toml"
    cfgfile.write_text(f"""
[quilt]
base = "main"
branches = ["feat-conflict"]

[llm]
resolve_cmd = "{stub}"
""")
    dbfile = tmp_path / "q.sqlite3"   # same DB the fixture populated
    cli.main(["--repo", str(r.path), "--config", str(cfgfile),
              "--db", str(dbfile), "resolve"])
    out = capsys.readouterr().out
    assert "resolved=1" in out
