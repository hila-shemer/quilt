"""Test-integrity auditor (Loom spec §6.1).

Validates that a green signal is *real* before it is trusted anywhere. The
keystone of Loom: the system amplifies both the value of coverage and the blast
radius of its gaps, so this auditor is load-bearing, not optional.

Deterministic checks run first (and are each independently fatal). Only when the
deterministic checks are *inconclusive* (a novel harness output we cannot parse)
is the decision hook (Haiku) consulted to classify real-green vs fake-green.
"""
import re
import time
from dataclasses import dataclass, field

from .. import gitio, llm


@dataclass
class GateRun:
    subject_id: str                 # merge-point id (quilt) or commit sha (Loom per-commit)
    gate: str
    tree_sha: str                   # tree the binary was supposedly built from
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    expected_tests: int | None = None
    coverage_paths: list[str] = field(default_factory=list)
    diff_paths: list[str] = field(default_factory=list)
    built_tree_sha: str | None = None   # tree actually checked out for the build, if observed
    is_test: bool = True                # False for build/compile gates that run no tests


@dataclass
class Verdict:
    real_green: bool
    reason: str
    inconclusive: bool = False      # True → the decision hook was consulted


# --- summary parsing --------------------------------------------------------

_COUNT_PATTERNS = {
    "passed": r"(\d+)\s+passed",
    "failed": r"(\d+)\s+failed",
    "errors": r"(\d+)\s+errors?",
    "skipped": r"(\d+)\s+skipped",
    "xfailed": r"(\d+)\s+xfailed",
    "deselected": r"(\d+)\s+deselected",
}
_BAZEL_EXECUTED = re.compile(r"Executed\s+(\d+)\s+out of\s+(\d+)\s+test", re.I)


def _parse_counts(text: str) -> dict | None:
    """Parse a test-harness summary into counts. Returns None if no recognizable
    token is present (→ inconclusive, defer to the decision hook)."""
    counts: dict[str, int] = {}
    for key, pat in _COUNT_PATTERNS.items():
        m = re.search(pat, text, re.I)
        if m:
            counts[key] = int(m.group(1))
    m = _BAZEL_EXECUTED.search(text)
    if m:
        counts["executed_bazel"] = int(m.group(1))
    return counts or None


def _executed(counts: dict) -> int:
    return (counts.get("passed", 0) + counts.get("failed", 0)
            + counts.get("errors", 0) + counts.get("executed_bazel", 0))


def _skipped_total(counts: dict) -> int:
    return counts.get("skipped", 0) + counts.get("xfailed", 0) + counts.get("deselected", 0)


# --- deterministic checks ---------------------------------------------------

def _deterministic(run: GateRun) -> Verdict | None:
    """Run the deterministic checks in order; return a fatal Verdict on the first
    failure, a Verdict(inconclusive=True) when the summary is unparseable, or
    None when every deterministic check passes (caller declares real-green)."""
    # 1. exit code: a green claim with nonzero exit is fake.
    if run.exit_code != 0:
        return Verdict(False, f"nonzero exit ({run.exit_code}) on a green claim")

    # 2. rebuilt-from-candidate-tree: the binary must come from the candidate tree.
    if run.built_tree_sha is not None and run.built_tree_sha != run.tree_sha:
        return Verdict(False,
                       f"binary built from tree {run.built_tree_sha[:12]} != candidate {run.tree_sha[:12]}")

    # A build/compile gate runs no tests: only exit-code + built-from-tree apply.
    if not run.is_test:
        return None

    # 3. parse the harness summary; unparseable → inconclusive (decision hook).
    counts = _parse_counts(run.stdout + "\n" + run.stderr)
    if counts is None:
        return Verdict(False, "unparseable harness summary", inconclusive=True)

    executed = _executed(counts)

    # 4. no tests executed — fake (distinguish all-skipped from truly nothing).
    if executed == 0:
        if _skipped_total(counts) > 0:
            return Verdict(False, "green only because all tests were skipped/xfailed/deselected")
        return Verdict(False, "no tests executed")

    # 5. expected-vs-actual count.
    if run.expected_tests is not None and executed < run.expected_tests:
        return Verdict(False, f"ran {executed} < expected {run.expected_tests} tests")

    # 6. coverage intersected the diff (only when coverage was reported).
    if run.diff_paths and run.coverage_paths and not (set(run.coverage_paths) & set(run.diff_paths)):
        return Verdict(False, "coverage did not intersect the diff (tests didn't exercise the change)")

    return None


# --- decision hook ----------------------------------------------------------

def _decide_hook(run: GateRun, cfg) -> Verdict:
    """Haiku classifies real-green vs fake-green for novel harness output. Only
    reached when the deterministic checks are inconclusive. Fails closed."""
    cmd = (cfg.llm or {}).get("audit_cmd") if cfg is not None else None
    if not cmd:
        # No hook configured: stay strict — an unverifiable green is not trusted.
        return Verdict(False, "inconclusive and no audit_cmd configured → treat as fake",
                       inconclusive=True)
    prompt = (
        "You are a test-integrity auditor. Decide whether a CI gate that exited 0 is a "
        "REAL green (tests actually ran and exercised the change) or a FAKE green "
        "(no tests ran, all skipped, harness aborted, etc.).\n"
        f"gate: {run.gate}\nexit_code: {run.exit_code}\n"
        f"expected_tests: {run.expected_tests}\n"
        f"diff_paths: {run.diff_paths}\ncoverage_paths: {run.coverage_paths}\n"
        f"--- harness output (tail) ---\n{(run.stdout + run.stderr)[-4000:]}\n"
        'Reply with JSON: {"real_green": bool, "reason": "<one sentence>"}'
    )
    try:
        out = llm.run_json(cmd, prompt)
    except llm.LLMError as e:
        return Verdict(False, f"auditor LLM error → treat as fake: {e}", inconclusive=True)
    return Verdict(bool(out.get("real_green", False)),
                   str(out.get("reason", "")), inconclusive=True)


# --- public API -------------------------------------------------------------

def audit(run: GateRun, db, cfg, *, repo=None) -> Verdict:
    """Classify a gate run as real-green or fake-green. Deterministic first;
    Haiku only when inconclusive. Persists the verdict to audit_result when a db
    is supplied."""
    v = _deterministic(run)
    if v is None:
        v = Verdict(True, "deterministic checks passed")
    elif v.inconclusive:
        v = _decide_hook(run, cfg)

    if db is not None:
        db.conn.execute(
            """INSERT INTO audit_result
                 (subject_id, gate, tree_sha, real_green, inconclusive, reason, created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(subject_id, gate, tree_sha) DO UPDATE SET
                 real_green=excluded.real_green, inconclusive=excluded.inconclusive,
                 reason=excluded.reason, created_at=excluded.created_at""",
            (run.subject_id, run.gate, run.tree_sha, int(v.real_green),
             int(v.inconclusive), v.reason, int(time.time())))
        db.conn.commit()
    return v


def verify_gate(repo, db, cfg, run: GateRun) -> Verdict:
    """The §6.1 interface: run after a gate-ladder execution. On a fake-green,
    void the cache entry and re-enqueue the gate so it cannot land. Returns the
    verdict so the caller can break the ladder.

    Voids quilt's per-merge-point gate cache when `run.subject_id` is a known
    merge-point (keyed by its base commit). Loom's per-commit cache (P2) wraps
    this and voids its own (tree_sha, gate) row."""
    v = audit(run, db, cfg, repo=repo)
    if not v.real_green:
        mp = db.get_merge_point(run.subject_id)
        if mp is not None:
            db.record_gate(run.subject_id, run.gate, mp["base_commit_sha"], "fail")
        db.enqueue_work("test_fail", run.subject_id, f"fake-green voided: {v.reason}")
    return v
