"""Linearizer: materialize an ordered increment set into an all-commits-green,
bisectable series on refs/loom/staging (Loom spec §6.2, principles §2.1/§2.3).

The published branch is the MAXIMAL GREEN PREFIX of the solved order: everything
from the first red seam onward is parked as a work item, never materialized.
Green by truncation, never by silent fixing.

An increment's payload is a single commit at `increment.patches["self"]`, applied
to the running tip by cherry-pick (the rebase-based composition). Task 5 wraps
solve() with the seam classifier (reorder-before-repair); Task 6 adds cycle
handling. This module (Task 4) does the deterministic materialize + truncate.
"""
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .. import gitio
from . import commitcache, decide, increments
from .worktree import WorktreePool

STAGING_REF = "refs/loom/staging"


@dataclass
class Solution:
    order: list[str]                       # increment ids, solved order
    landed: list[str] = field(default_factory=list)   # ids on the green prefix
    staging_tip: str = ""                  # sha at refs/loom/staging
    seam: str | None = None                # increment id at the first red seam
    seam_kind: str | None = None           # 'conflict' | 'test-fail'
    commits: dict = field(default_factory=dict)        # inc id -> landed commit sha

    def commit_of(self, inc_id: str) -> str | None:
        return self.commits.get(inc_id)


def _cherry_pick(wt: Path, commit: str) -> bool:
    """Apply *commit*'s diff onto the worktree's HEAD. Returns False (and aborts)
    on conflict."""
    p = subprocess.run(["git", "-C", str(wt), "cherry-pick", "--allow-empty", commit],
                       capture_output=True, text=True)
    if p.returncode != 0:
        subprocess.run(["git", "-C", str(wt), "cherry-pick", "--abort"],
                       capture_output=True, text=True)
        return False
    return True


def _fully_green(cfg, highest: str | None) -> bool:
    last = cfg.gates[-1]["name"] if cfg.gates else None
    return highest == last


def solve(repo: Path, db, cfg, incs: list, pool: WorktreePool | None = None) -> Solution:
    """Materialize `incs` in dependency+policy order; publish the maximal green
    prefix to refs/loom/staging; park the seam and everything after it."""
    pool = pool or WorktreePool(repo, size=4)
    edges = increments.list_dep_edges(db)
    ordered = increments.order(incs, edges)
    sol = Solution(order=[i.id for i in ordered])

    base_sha = gitio.rev(repo, cfg.base)
    gitio.update_ref(repo, STAGING_REF, base_sha)
    sol.staging_tip = base_sha
    tip = base_sha

    for idx, inc in enumerate(ordered):
        commit = inc.patches["self"]
        # apply
        with pool.checkout(tip) as wt:
            if not _cherry_pick(wt, commit):
                sol.seam, sol.seam_kind = inc.id, "conflict"
                break
            new_tip = gitio.rev(wt, "HEAD")
        # gate the resulting commit
        highest = commitcache.run_ladder_on_commit(repo, db, cfg, new_tip, pool)
        if not _fully_green(cfg, highest):
            sol.seam, sol.seam_kind = inc.id, "test-fail"
            break
        # green: extend the prefix and publish progressively (crash-resumable)
        tip = new_tip
        sol.landed.append(inc.id)
        sol.commits[inc.id] = new_tip
        sol.staging_tip = new_tip
        gitio.update_ref(repo, STAGING_REF, new_tip)
        increments.set_status(db, inc.id, "green")

    # park the seam and everything after it (never materialized)
    if sol.seam is not None:
        seam_pos = sol.order.index(sol.seam)
        for inc_id in sol.order[seam_pos:]:
            increments.set_status(db, inc_id, "parked")
        if sol.seam_kind == "conflict":
            db.enqueue_work("conflict", sol.seam, "linearizer seam: cherry-pick conflict")
    return sol


def _reset_status(db, incs) -> None:
    for inc in incs:
        increments.set_status(db, inc.id, "building")


def _classify_seam(repo: Path, db, cfg, sol: Solution, incs: list) -> dict:
    """The one judgment a script can't make (spec §6.2 decision hook): at a red
    seam, is this a hard-dependency (→ reorder, free) or an incidental-conflict
    (→ repair, debt)? Haiku: (seam diff) -> {kind: hard|incidental, easy_fix}.
    Defaults to 'incidental' when no classifier is configured or it errors —
    the safe choice (don't thrash reordering on a guess)."""
    seam_inc = next(i for i in incs if i.id == sol.seam)
    diff = gitio.git(repo, "show", "--stat", "--patch", seam_inc.patches["self"])
    prompt = (
        "A linearizer seam: applying this increment on the current prefix was red "
        f"(kind={sol.seam_kind}). Classify whether the increment has a HARD dependency "
        "on another increment that must precede it (reorder fixes it), or an INCIDENTAL "
        "conflict that needs a code fix.\n"
        f"--- increment diff ---\n{diff[:6000]}\n"
        'Reply JSON: {"kind": "hard"|"incidental", "easy_fix": bool}'
    )
    verdict = decide.decide_json(cfg, "seam", prompt, db=db, task_type="seam")
    return verdict or {"kind": "incidental", "easy_fix": False}


def solve_seams(repo: Path, db, cfg, incs: list, pool: WorktreePool | None = None,
                max_reorders: int = 8) -> Solution:
    """Wrap solve() with the seam classifier: on a red seam, try REORDER before
    REPAIR. A 'hard' verdict records dep_edge(seam -> each later increment) and
    re-solves (reorder is non-destructive); the re-solve is the deterministic
    re-check (§7) — if the seam can't be cleared by reordering it falls through
    to repair (park). 'incidental' parks immediately."""
    pool = pool or WorktreePool(repo, size=4)
    sol = None
    for attempt in range(max_reorders + 1):
        _reset_status(db, incs)
        sol = solve(repo, db, cfg, incs, pool)
        if sol.seam is None:
            return sol
        verdict = _classify_seam(repo, db, cfg, sol, incs)
        later = sol.order[sol.order.index(sol.seam) + 1:]
        if verdict.get("kind") == "hard" and later and attempt < max_reorders:
            for y in later:
                increments.add_dep_edge(db, sol.seam, y, evidence="seam: hard-dep")
            continue                       # reorder, then re-solve (before any repair)
        return sol                          # incidental / no candidate / budget spent → park
    return sol
