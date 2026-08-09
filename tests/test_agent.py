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

CONCLUDE_STUB = RESOLVE_STUB + """git add -A
git commit -qm "agent: resolve merge"
"""


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


def test_agent_resolves_multi_tip_conflicts(tmp_path, repo_with_branches, db):
    """Two member branches each conflict (one vs base edit, one vs the other);
    the agent stub runs once per conflicted merge step."""
    r = repo_with_branches
    r.branch("feat-conflict2")
    r.commit_file("base.txt", "line1\nline2\nOTHER\n")     # edits line3
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")      # base moves: line2
    results = probe.probe_all(r.path, "main",
                              ["feat-conflict", "feat-conflict2"], db)
    pair = next(x for x in results if len(x["branches"]) == 2)
    assert pair["construction"] == "conflict"
    db.enqueue_work("conflict", pair["id"], "multi conflict")
    item = db.pending_work()[0]
    db.set_work_state(item["id"], "triaged")
    cfg_path = tmp_path / "multi.toml"
    stub = make_stub(tmp_path, "a.sh", RESOLVE_STUB)
    cfg_path.write_text(f"""
[quilt]
base = "main"
branches = ["feat-conflict", "feat-conflict2"]

[llm]
resolve_cmd = "{stub}"
""")
    cfg = gates.load_config(cfg_path)
    assert agent.resolve_conflict(r.path, db, cfg, dict(item)) is True
    mp = db.get_merge_point(pair["id"])
    assert mp["construction"] == "agent"
    merged = r.git("show", f"{mp['result_commit']}:base.txt")
    assert "MAIN" in merged and "CONFLICT" in merged and "OTHER" in merged


def test_agent_concluded_merge_without_glue_is_agent(tmp_path, conflict_item):
    r, db, item = conflict_item
    cfg = _cfg(tmp_path, make_stub(tmp_path, "a.sh", CONCLUDE_STUB))
    assert agent.resolve_conflict(r.path, db, cfg, item) is True
    mp = db.get_merge_point(item["target_id"])
    assert mp["construction"] == "agent"          # no glue commits
    assert db.list_fixes() == []


DIAG_RESOLUTION_STUB = ('#!/bin/sh\ncat >/dev/null\n'
    'echo \'{"attribution": "resolution", "culprit": "", "reason": "bad hunk"}\'\n')
DIAG_MEMBER_STUB = ('#!/bin/sh\ncat >/dev/null\n'
    'echo \'{"attribution": "member", "culprit": "abc123", "reason": "feature bug"}\'\n')


def _diag_cfg(tmp_path, stub_path):
    p = tmp_path / "d.toml"
    p.write_text(f"""
[quilt]
base = "main"
branches = ["feat-clean", "feat-conflict"]

[llm]
diagnose_cmd = "{stub_path}"
""")
    return gates.load_config(p)


@pytest.fixture
def failed_item(repo_with_branches, db):
    """Probe a clean pair, then fake a gate failure on the single-member
    subset so poison can cascade to the pair."""
    r = repo_with_branches
    results = probe.probe_all(r.path, "main", ["feat-clean", "feat-conflict"], db)
    by_n = sorted(results, key=lambda x: len(x["branches"]))
    single, pair = by_n[0], by_n[-1]
    db.enqueue_work("test_fail", single["id"], "compiles: boom")
    item = db.pending_work()[0]
    db.set_work_state(item["id"], "triaged")
    return r, db, dict(item, state="triaged"), single["id"], pair["id"]


def test_diagnose_resolution_poisons_and_evicts(tmp_path, failed_item):
    r, db, item, single_id, pair_id = failed_item
    cfg = _diag_cfg(tmp_path, make_stub(tmp_path, "d.sh", DIAG_RESOLUTION_STUB))
    verdict = agent.diagnose_failure(r.path, db, cfg, item)
    assert verdict["attribution"] == "resolution"
    assert db.get_merge_point(single_id)["validation_state"] == "poison"
    assert db.get_merge_point(pair_id)["validation_state"] == "untested"
    assert gitio.read_ref(r.path, f"refs/quilt/{pair_id}") is None  # evicted
    assert db.work_by_state("done")[0]["id"] == item["id"]


def test_diagnose_member_does_not_poison(tmp_path, failed_item):
    r, db, item, single_id, pair_id = failed_item
    cfg = _diag_cfg(tmp_path, make_stub(tmp_path, "d.sh", DIAG_MEMBER_STUB))
    verdict = agent.diagnose_failure(r.path, db, cfg, item)
    assert verdict["attribution"] == "member"
    assert db.get_merge_point(single_id)["validation_state"] == "untested"
    assert gitio.read_ref(r.path, f"refs/quilt/{pair_id}") is not None
    assert db.work_by_state("done")[0]["id"] == item["id"]


def test_diagnose_garbage_keeps_item(tmp_path, failed_item):
    r, db, item, single_id, _ = failed_item
    cfg = _diag_cfg(tmp_path, make_stub(tmp_path, "d.sh",
                                        "#!/bin/sh\ncat >/dev/null\necho junk\n"))
    assert agent.diagnose_failure(r.path, db, cfg, item) is None
    assert db.work_by_state("triaged")[0]["id"] == item["id"]


def test_diagnose_cli(tmp_path, failed_item, capsys):
    from quilt import cli
    r, db, item, single_id, _ = failed_item
    stub = make_stub(tmp_path, "d.sh", DIAG_RESOLUTION_STUB)
    cfgfile = tmp_path / "d.toml"
    cfgfile.write_text(f"""
[quilt]
base = "main"
branches = ["feat-clean", "feat-conflict"]

[llm]
diagnose_cmd = "{stub}"
""")
    cli.main(["--repo", str(r.path), "--config", str(cfgfile),
              "--db", str(tmp_path / "q.sqlite3"), "diagnose"])
    out = capsys.readouterr().out
    assert "diagnosed=1" in out
    assert "resolution" in out


ECHO_PROMPT_STUB = ('#!/bin/sh\ncat > "$(dirname "$0")/prompt.txt"\n'
    'echo \'{"attribution": "member", "culprit": "abc123", "reason": "x"}\'\n')


def test_diagnose_prompt_names_the_gate_and_branches(tmp_path, failed_item):
    """The gate name and the member branches used to reach the diagnosing agent
    only by accident, glued onto the head of the detail blob."""
    r, db, item, single_id, _ = failed_item
    db.conn.execute("UPDATE work_queue SET gate='tests', exit_code=2 WHERE id=?",
                    (item["id"],))
    db.conn.commit()
    item = db.get_work(item["id"])
    cfg = _diag_cfg(tmp_path, make_stub(tmp_path, "d.sh", ECHO_PROMPT_STUB))
    assert agent.diagnose_failure(r.path, db, cfg, item) is not None
    prompt = (tmp_path / "prompt.txt").read_text()
    assert "tests" in prompt
    assert "exit 2" in prompt
    assert "feat-clean" in prompt
