"""Coverage gate + seam-coverage certification (Loom spec §6.8) — no LLM.

Two jobs:

1. **A coverage bar as a ladder rung.** `gate()` runs the configured coverage
   command on a commit, parses the report, and passes iff the measured coverage
   meets the bar — otherwise the ladder breaks and a `coverage_fail` work item is
   enqueued. (This is a measurement gate, not a test gate, so it does not route
   through the test-summary auditor — there is no test summary to parse.)

2. **Validity input for P2's inferred DAG.** A dependency edge `X→Y` is only
   trustworthy if the seam test that would catch the dependency actually executed
   the relevant code. `certify_edges()` cross-checks each edge's witnessing paths
   (stored in `dep_edge.evidence`) against the executed paths and writes back the
   `witnessed` flag; an unwitnessed edge must not be trusted for reorder.
"""
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import increments
from .worktree import WorktreePool

_TOTAL_PCT = re.compile(r"TOTAL\b.*?(\d+(?:\.\d+)?)\s*%", re.I)


@dataclass
class Report:
    percent: float
    covered_paths: list[str] = field(default_factory=list)


def parse_report(text: str) -> Report | None:
    """Parse a coverage report. Accepts a JSON object
    `{"percent": float, "covered_paths": [...]}` or a coverage.py-style text
    table with a `TOTAL ... NN%` line. Returns None if nothing parseable."""
    text = text.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "percent" in obj:
            return Report(float(obj["percent"]),
                          [str(p) for p in obj.get("covered_paths", [])])
    except (ValueError, TypeError):
        pass
    m = _TOTAL_PCT.search(text)
    return Report(float(m.group(1))) if m else None


def meets_bar(report: Report, bar: float) -> bool:
    return report.percent >= float(bar)


def gate(repo: Path, db, cfg, commit: str, gate_cfg: dict,
         pool: WorktreePool | None = None) -> str:
    """Run the coverage command on *commit*; 'pass' iff coverage meets the bar.
    On pass, certify dep-edge witnessing from the report; on fail, enqueue."""
    pool = pool or WorktreePool(repo, size=1)
    bar = float(gate_cfg.get("bar", 0.0))
    with pool.checkout(commit) as wt:
        cmd = gate_cfg["cmd"].replace("{workdir}", str(wt))
        proc = subprocess.run(cmd, shell=True, cwd=wt, capture_output=True, text=True)
    report = parse_report(proc.stdout + "\n" + proc.stderr)
    if proc.returncode != 0 or report is None or not meets_bar(report, bar):
        pct = "unparseable" if report is None else f"{report.percent}%"
        db.enqueue_work("coverage_fail", commit, f"coverage {pct} < bar {bar}")
        return "fail"
    certify_edges(db, report.covered_paths)
    return "pass"


# --- seam-coverage certification --------------------------------------------

def _witness_paths(evidence: str | None) -> list[str]:
    """Extract the witnessing paths from a dep_edge's evidence. A JSON list is
    used verbatim; otherwise path-like tokens (containing '/' or '.') are taken.
    Free-text evidence with no path yields no witnesses → cannot be certified."""
    if not evidence:
        return []
    try:
        v = json.loads(evidence)
        if isinstance(v, list):
            return [str(p) for p in v]
    except (ValueError, TypeError):
        pass
    return [t for t in re.split(r"[,\s]+", evidence.strip()) if "/" in t or "." in t]


def certify_edges(db, covered_paths) -> list[dict]:
    """Mark each dep_edge `witnessed` iff its witnessing paths were all executed.
    Returns the per-edge certification results."""
    covered = set(covered_paths)
    results = []
    for e in increments.list_dep_edges(db):
        wp = _witness_paths(e.get("evidence"))
        witnessed = bool(wp) and set(wp) <= covered
        db.conn.execute("UPDATE dep_edge SET witnessed=? WHERE x=? AND y=?",
                        (int(witnessed), e["x"], e["y"]))
        results.append({"x": e["x"], "y": e["y"], "witnessed": witnessed})
    db.conn.commit()
    return results
