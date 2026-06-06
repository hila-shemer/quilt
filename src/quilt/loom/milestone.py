"""Milestone selection + frozen floor (Loom spec §3, §6.6, decision §13).

A **milestone** defaults to the tip of a fully-absorbed increment (decision §13).
In the single-commit-per-increment model that `linearize.solve` materializes,
that is one staging commit per green increment; an increment that contributes a
commit range (the cycle-interleave path) collapses to a single milestone at its
tip, so the intermediate commits are not stress points (wider spacing = fewer
stress runs).

`next_staging`'s tip is the **frozen floor**: staging may rewrite only the suffix
above it. `mutable_suffix` is exactly that mutable window — the seam-search
working set — and shrinks from below as milestones validate.
"""
from pathlib import Path

from .. import gitio
from . import increments, linearize

STAGING_REF = linearize.STAGING_REF
NEXT_STAGING_REF = "refs/loom/next_staging"


def milestones(repo: Path, db, cfg, staging_ref: str = STAGING_REF) -> list[str]:
    """Per-increment tip commits on the staging series, in landing order.

    Walks `cfg.base..staging` and marks increment boundaries from the increment
    store (the green set in solved order), so only absorbed-increment tips are
    returned — never an intermediate commit of a multi-commit increment."""
    tip = gitio.read_ref(repo, staging_ref)
    if not tip:
        return []
    base = gitio.rev(repo, cfg.base)
    commits = _rev_list(repo, f"{base}..{tip}")
    green = increments.list_all(db, status="green")
    ordered = increments.order(green, increments.list_dep_edges(db))
    tips, i = [], 0
    for inc in ordered:
        i += len(linearize._inc_commits(repo, inc))
        if i - 1 < len(commits):
            tips.append(commits[i - 1])
    return tips


def mutable_suffix(repo: Path, next_staging_ref: str = NEXT_STAGING_REF,
                   staging_ref: str = STAGING_REF, base: str | None = None) -> list[str]:
    """Commits in `next_staging..staging` (oldest→newest) — the rewritable window
    above the frozen floor. With no floor ref, the floor is `base` (whole series
    is mutable)."""
    tip = gitio.read_ref(repo, staging_ref)
    if not tip:
        return []
    floor = gitio.read_ref(repo, next_staging_ref) or base
    rng = f"{floor}..{tip}" if floor else tip
    return _rev_list(repo, rng)


def _rev_list(repo: Path, rng: str) -> list[str]:
    out = gitio.git(repo, "rev-list", "--reverse", rng)
    return [c for c in out.splitlines() if c.strip()]
