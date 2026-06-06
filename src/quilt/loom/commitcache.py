"""Per-commit gate cache + audited per-commit gate runner (Loom spec §4.3, §6.2).

Serves the linear line (staging). Keyed on the commit's *tree*, so two commits
with the same tree share a cached pass — the property that makes a fast-forward
into next_staging a pure cache-hit operation (P3). Every fresh run is routed
through the test-integrity auditor (P1); only a real-green is cached, so a
fake-green can never land in the cache.
"""
import subprocess
import time
from pathlib import Path

from .. import gitio
from . import audit
from .worktree import WorktreePool


def commit_gate_result(db, tree_sha: str, gate: str) -> str | None:
    row = db.conn.execute(
        "SELECT status FROM commit_gate WHERE tree_sha=? AND gate=?",
        (tree_sha, gate)).fetchone()
    return row["status"] if row else None


def _record(db, tree_sha: str, gate: str, status: str, result_ref=None) -> None:
    db.conn.execute(
        """INSERT INTO commit_gate (tree_sha, gate, status, result_ref, finished_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(tree_sha, gate) DO UPDATE SET
             status=excluded.status, result_ref=excluded.result_ref,
             finished_at=excluded.finished_at""",
        (tree_sha, gate, status, result_ref, int(time.time())))
    db.conn.commit()


def _changed_paths(repo: Path, commit: str) -> list[str]:
    out = gitio.git(repo, "show", "--name-only", "--pretty=format:", commit)
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def run_commit_gate(repo: Path, db, cfg, commit: str, gate_cfg: dict,
                    pool: WorktreePool | None = None) -> str:
    """Run one gate on one commit. Returns 'pass' or 'fail'. Cache-hits on a
    previously-recorded pass for the same tree without re-running. Fresh runs
    are auditor-verified; a fake-green is recorded as fail and re-enqueued."""
    name = gate_cfg["name"]
    tree = gitio.tree_of(repo, commit)
    if commit_gate_result(db, tree, name) == "pass":
        return "pass"

    pool = pool or WorktreePool(repo, size=1)
    sha = gitio.rev(repo, commit)
    with pool.checkout(commit) as wt:
        built_tree = gitio.tree_of(wt, "HEAD")
        cmd = gate_cfg["cmd"].replace("{workdir}", str(wt))
        proc = subprocess.run(cmd, shell=True, cwd=wt, capture_output=True, text=True)

    run = audit.GateRun(
        subject_id=sha, gate=name, tree_sha=tree, built_tree_sha=built_tree,
        exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
        expected_tests=gate_cfg.get("expected"),
        coverage_paths=gate_cfg.get("_coverage_paths", []),   # P4 wires real coverage
        diff_paths=_changed_paths(repo, commit),
        is_test=gate_cfg.get("test", True))
    verdict = audit.audit(run, db, cfg)

    if verdict.real_green:
        _record(db, tree, name, "pass")
        return "pass"
    db.enqueue_work("test_fail", sha, f"{name}: {verdict.reason}")
    return "fail"


def run_ladder_on_commit(repo: Path, db, cfg, commit: str,
                         pool: WorktreePool | None = None) -> str | None:
    """Run the configured ladder bottom-up over one commit; stop at the first
    non-pass. Returns the highest passed gate name (or None)."""
    pool = pool or WorktreePool(repo, size=1)
    highest = None
    for gate_cfg in cfg.gates:
        if run_commit_gate(repo, db, cfg, commit, gate_cfg, pool) != "pass":
            break
        highest = gate_cfg["name"]
    return highest
