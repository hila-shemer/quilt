"""FF promotion + milestone stress (Loom spec §6.6).

Advances `next_staging` by **fast-forward from staging** at a validated milestone,
paying only the marginal cost of the stress (`long`) gate. This composes quilt's
`candidate.freeze`/`advance` semantics onto the linear line:

  freeze   — write `refs/loom/candidate/next_staging` at the chosen milestone
  stress   — run the `long` gate on that commit through the per-commit cache
             (auditor-verified; tree-keyed so a fast-forward is a cache-hit op —
             the candidate-ladder gates already ran during solve and are NOT
             re-run, only the new `long` gate is marginal work)
  advance  — on real-green, fast-forward `refs/loom/next_staging` (FF-only: the
             frozen floor never rewrites). On stress-fail, HOLD the floor, let
             staging run ahead, and the routed `test_fail` work item carries
             attribution to the debugger (§6.10) for a fix on the mutable suffix.

No LLM on this path (the auditor's deterministic checks only).
"""
from pathlib import Path

from .. import gitio
from . import commitcache, milestone
from .worktree import WorktreePool

CANDIDATE_REF = "refs/loom/candidate/next_staging"
NEXT_STAGING_REF = milestone.NEXT_STAGING_REF


class NonFastForward(Exception):
    """A promotion target that does not descend from the frozen floor; refused so
    the floor never rewrites (spec §6.6)."""


def _stress_gate(cfg) -> dict:
    return (cfg.promotion or {}).get(
        "stress", {"name": "long", "test": False, "cmd": "true"})


def _is_ancestor(repo: Path, anc: str, desc: str) -> bool:
    return gitio._run(repo, "merge-base", "--is-ancestor", anc, desc,
                      check=False).returncode == 0


def _fast_forward(repo: Path, ref: str, target: str) -> None:
    """Advance *ref* to *target* iff it is a true fast-forward; else refuse."""
    cur = gitio.read_ref(repo, ref)
    if cur is not None and not _is_ancestor(repo, cur, target):
        raise NonFastForward(f"{ref} ({cur[:12]}) is not an ancestor of {target[:12]}")
    gitio.update_ref(repo, ref, target)


def _next_milestone(repo: Path, db, cfg) -> str | None:
    """The next stress target: the lowest milestone strictly above the floor."""
    ms = milestone.milestones(repo, db, cfg)
    if not ms:
        return None
    floor = gitio.read_ref(repo, NEXT_STAGING_REF)
    if floor is None:
        return ms[0]
    above = [m for m in ms if m != floor and _is_ancestor(repo, floor, m)]
    return above[0] if above else None


def run(repo: Path, db, cfg, pool: WorktreePool | None = None) -> dict | None:
    """Promote one milestone. Returns {promoted, milestone} or None when there is
    nothing above the floor to promote."""
    target = _next_milestone(repo, db, cfg)
    if target is None:
        return None
    pool = pool or WorktreePool(repo, size=1)

    gitio.update_ref(repo, CANDIDATE_REF, target)          # freeze
    result = commitcache.run_commit_gate(repo, db, cfg, target, _stress_gate(cfg), pool)
    if result != "pass":                                   # hold floor; let staging run ahead
        return {"promoted": False, "milestone": target}    # run_commit_gate enqueued test_fail
    _fast_forward(repo, NEXT_STAGING_REF, target)          # advance (FF-only)
    return {"promoted": True, "milestone": target}
