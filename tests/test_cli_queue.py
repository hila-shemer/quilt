"""Reading a failure must not require opening .quilt.sqlite3.

Every test here is a line from the sahara complaint: the queue showed the
passing prefix of a log, merge-points were anonymous hashes, a gate that failed
for the gate's own reasons had no exit but a database reset, and a merge-point
whose gate had failed was displayed as untested.
"""
import pytest
from quilt import cli
from quilt.db import DB

CFG = """
[quilt]
base = "main"
branches = ["feat-clean"]

[[gate]]
name = "compiles"
cmd = 'true'

[[gate]]
name = "tests"
cmd = 'echo "ok: alpha"; echo "ok: beta"; echo "FAIL: gamma exploded" >&2; exit 2'

[targets]
next = "compiles"
"""


@pytest.fixture
def cfgfile(tmp_path):
    p = tmp_path / "quilt.toml"
    p.write_text(CFG)
    return p


def run(args, repo, cfgfile, capsys):
    cli.main(["--repo", str(repo.path), "--config", str(cfgfile)] + args)
    return capsys.readouterr().out


def run_fails(args, repo, cfgfile, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--repo", str(repo.path), "--config", str(cfgfile)] + args)
    return exc.value.code, capsys.readouterr().out


@pytest.fixture
def ticked(repo_with_branches, cfgfile, capsys):
    run(["tick"], repo_with_branches, cfgfile, capsys)
    return repo_with_branches


def db_of(cfgfile):
    return DB(cfgfile.parent / ".quilt.sqlite3")


# --- relief 1: the queue line says what failed, and about which branches ---

def test_queue_names_the_member_branches(ticked, cfgfile, capsys):
    assert "feat-clean" in run(["queue"], ticked, cfgfile, capsys)


def test_queue_reports_gate_and_exit_code(ticked, cfgfile, capsys):
    out = run(["queue"], ticked, cfgfile, capsys)
    assert "gate=tests" in out
    assert "exit=2" in out


def test_queue_shows_the_failure_not_the_passing_prefix(ticked, cfgfile, capsys):
    out = run(["queue"], ticked, cfgfile, capsys)
    assert "FAIL: gamma exploded" in out
    assert "ok: alpha" not in out


def test_queue_line_budget_is_configurable(ticked, cfgfile, capsys):
    """--lines bounds the log excerpt; the '… omitted' marker is not an excerpt."""
    out = run(["queue", "--lines", "1"], ticked, cfgfile, capsys)
    body = [ln.strip() for ln in out.strip().splitlines()[1:]]
    assert [ln for ln in body if not ln.startswith("…")] == ["FAIL: gamma exploded"]


# --- relief 2: the whole detail, without the database ---

def test_show_emits_the_complete_stored_detail(ticked, cfgfile, capsys):
    out = run(["show", "1"], ticked, cfgfile, capsys)
    assert "ok: alpha" in out
    assert "ok: beta" in out
    assert "FAIL: gamma exploded" in out


def test_queue_full_emits_the_complete_stored_detail(ticked, cfgfile, capsys):
    out = run(["queue", "--full"], ticked, cfgfile, capsys)
    assert "ok: alpha" in out


def test_show_unknown_id_exits_1(ticked, cfgfile, capsys):
    code, out = run_fails(["show", "999"], ticked, cfgfile, capsys)
    assert code == 1
    assert "999" in out


# --- relief 4: an exit for gate-environment failures ---

def test_dismiss_clears_the_item_without_poisoning(ticked, cfgfile, capsys):
    db = db_of(cfgfile)
    [mp] = db.list_merge_points()
    out = run(["dismiss", "1", "--reason", "bazel dep path"], ticked, cfgfile, capsys)
    assert "dismissed" in out
    assert db_of(cfgfile).pending_work() == []
    assert db_of(cfgfile).get_merge_point(mp["id"])["validation_state"] != "poison"


def test_dismiss_records_the_reason(ticked, cfgfile, capsys):
    run(["dismiss", "1", "--reason", "bazel dep path"], ticked, cfgfile, capsys)
    assert "bazel dep path" in run(["show", "1"], ticked, cfgfile, capsys)


def test_dismissed_failure_returns_on_the_next_tick(ticked, cfgfile, capsys):
    """Dismissal is not a verdict — if the gate still fails, it comes back."""
    run(["dismiss", "1", "--reason", "pytest misconfiguration"], ticked, cfgfile, capsys)
    run(["tick"], ticked, cfgfile, capsys)
    assert db_of(cfgfile).pending_work() != []


def test_requeue_returns_a_triaged_item_to_the_queue(ticked, cfgfile, capsys):
    db_of(cfgfile).set_work_state(1, "triaged")
    out = run(["requeue", "1"], ticked, cfgfile, capsys)
    assert "requeued" in out
    assert [w["id"] for w in db_of(cfgfile).pending_work()] == [1]


def test_dismiss_unknown_id_exits_1(ticked, cfgfile, capsys):
    code, _ = run_fails(["dismiss", "999", "--reason", "x"], ticked, cfgfile, capsys)
    assert code == 1


# --- relief 5: status tells the truth, and idle is a predicate ---

def test_status_names_the_member_branches(ticked, cfgfile, capsys):
    assert "feat-clean" in run(["status"], ticked, cfgfile, capsys)


def test_status_shows_a_merge_point_with_a_failed_gate_as_failed(ticked, cfgfile,
                                                                 capsys):
    out = run(["status"], ticked, cfgfile, capsys)
    assert "failed" in out
    assert "untested" not in out


def test_status_names_the_gate_that_failed(ticked, cfgfile, capsys):
    assert "fail=tests" in run(["status"], ticked, cfgfile, capsys)


def test_idle_is_false_before_anything_is_probed(repo_with_branches, cfgfile, capsys):
    code, out = run_fails(["idle"], repo_with_branches, cfgfile, capsys)
    assert code == 1
    assert "not idle" in out


def test_idle_is_true_once_every_merge_point_is_decided(ticked, cfgfile, capsys):
    assert "idle" in run(["idle"], ticked, cfgfile, capsys)


def test_status_ends_with_the_idle_predicate(ticked, cfgfile, capsys):
    assert run(["status"], ticked, cfgfile, capsys).strip().splitlines()[-1] \
        .startswith("idle=")
