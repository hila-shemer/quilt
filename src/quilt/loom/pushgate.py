"""Propose-push gate — HARD SAFETY GATE (Loom spec §6.7, principle §2.6).

This module **never pushes.** It surfaces the materialized result + a proposed
branch name for interactive approval and verifies the push URL against the
`hila-shemer`-fork / not-`Majestic` rule (the same remote check rightwayc's
`tools/mergeq/green_verify.py:check_overrides` applies to a `git_override`
remote). On any block it emits `git format-patch` artifacts instead of a
proposal. `refs/loom/staging` (the local working line) is unconditionally
non-pushable.

There is **no `git push` call anywhere in this module**. If a proposal is
approved, pushing it is the operator's explicit action outside Loom (or a
separately-audited, explicitly-approved step) — by design, so no component can
auto-push a remote.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import gitio
from . import milestone

STAGING_REF = milestone.STAGING_REF              # refs/loom/staging — never pushable
NEXT_STAGING_REF = milestone.NEXT_STAGING_REF


class PushBlocked(Exception):
    """The push URL failed the hila-shemer/not-Majestic certification."""


class NotPushable(Exception):
    """The ref is the local working line (staging) and is never pushable."""


@dataclass
class PushProposal:
    url: str
    ref: str
    branch: str
    commit: str | None
    diffstat: str = ""
    blocked: bool = False
    reason: str = ""
    artifacts: list = field(default_factory=list)   # list[Path] of .patch files


def _url_block_reason(url: str) -> str | None:
    """Mirror green_verify.py's git_override remote rule: a remote is acceptable
    only when it names a hila-shemer fork and is not a Majestic remote."""
    if "hila-shemer" not in url or re.search("majestic", url, re.I):
        return f"remote {url!r} is not a hila-shemer fork (or is a Majestic remote)"
    return None


def _emit_patches(repo: Path, cfg, ref: str, outdir: Path) -> list[Path]:
    """`git format-patch` of base..ref into *outdir*; return the patch paths."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    out = gitio.git(repo, "format-patch", "-o", str(outdir), f"{cfg.base}..{ref}")
    return [Path(line.strip()) for line in out.splitlines() if line.strip()]


def propose(repo: Path, cfg, *, url: str, ref: str,
            outdir: Path | None = None, branch: str | None = None) -> PushProposal:
    """Surface a push proposal for interactive approval. Pushes nothing.

    - `refs/loom/staging` → `NotPushable` (unconditional, checked first).
    - a non-hila / Majestic URL → `PushBlocked`, unless *outdir* is given, in
      which case patch artifacts are emitted and a blocked proposal is returned.
    - otherwise → a `PushProposal` describing the would-be push (no push made)."""
    if ref == STAGING_REF:
        raise NotPushable(f"{STAGING_REF} is the local working line and is never pushable")

    reason = _url_block_reason(url)
    commit = gitio.read_ref(repo, ref)
    if reason:
        if outdir is not None:
            arts = _emit_patches(repo, cfg, ref, outdir)
            return PushProposal(url=url, ref=ref,
                                branch=branch or _branch_for(commit),
                                commit=commit, blocked=True, reason=reason,
                                artifacts=arts)
        raise PushBlocked(reason)

    diffstat = gitio.git(repo, "diff", "--stat", f"{cfg.base}..{ref}")
    return PushProposal(url=url, ref=ref, branch=branch or _branch_for(commit),
                        commit=commit, diffstat=diffstat)


def _branch_for(commit: str | None) -> str:
    return f"loom/promote-{commit[:12]}" if commit else "loom/promote"
