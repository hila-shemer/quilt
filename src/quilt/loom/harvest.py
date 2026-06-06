"""Regression-lock harvest (Loom spec §6.3) — no LLM.

Scans an injected branch for **test-only** commits that apply on the base and
pass the cheap gate ladder, lifts them onto the base (merge-immediately), and
rebases the donor so the lifted commits are dropped. The classifier and the
lift-condition coincide: a commit touching only test-paths AND green on the base
is liftable; anything else is left for the linearizer (P2).

A base-tree change invalidates the combination cache, so all lifts in one pass
are **batched behind a single reflow-epoch boundary** (`epoch.roll`), never
dribbled out as separate base rewrites.
"""
import fnmatch
from pathlib import Path

from .. import gitio
from . import commitcache, epoch as epoch_mod, linearize
from .worktree import WorktreePool

DEFAULT_TEST_GLOBS = ("tests/**", "test/**", "**/test_*.py", "**/*_test.py")


def _changed_paths(repo: Path, commit: str) -> list[str]:
    out = gitio.git(repo, "show", "--name-only", "--pretty=format:", commit)
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _is_test_only(paths: list[str], globs) -> bool:
    return bool(paths) and all(any(fnmatch.fnmatch(p, g) for g in globs) for p in paths)


def _fully_green(cfg, highest: str | None) -> bool:
    last = cfg.gates[-1]["name"] if cfg.gates else None
    return highest == last


def run(repo: Path, db, cfg, branch: str, test_globs=DEFAULT_TEST_GLOBS,
        pool: WorktreePool | None = None) -> list[str]:
    """Harvest test-only liftable commits from *branch* onto `cfg.base`. Returns
    the lifted base commit shas (post-lift identities)."""
    pool = pool or WorktreePool(repo, size=2)
    base_sha = gitio.rev(repo, cfg.base)
    candidates = _rev_list(repo, f"{base_sha}..{branch}")

    tip = base_sha
    lifted, lifted_src = [], set()
    for commit in candidates:
        if not _is_test_only(_changed_paths(repo, commit), test_globs):
            continue                              # non-test → leave for the linearizer
        with pool.checkout(tip) as wt:
            if not linearize._cherry_pick(wt, commit):
                continue                          # doesn't apply on base → leave it
            new_tip = gitio.rev(wt, "HEAD")
        if not _fully_green(cfg, commitcache.run_ladder_on_commit(repo, db, cfg, new_tip, pool)):
            db.enqueue_work("test_fail", commit, "harvest: test-only commit red on base")
            continue                              # red on base → queued, not lifted
        tip = new_tip                             # extend the running base
        lifted.append(new_tip)
        lifted_src.add(commit)

    if lifted:
        gitio.update_ref(repo, cfg.base, tip)     # merge immediately, batched
        epoch_mod.roll(db)                        # one epoch boundary for the base change
        replay = [c for c in candidates if c not in lifted_src]
        _rebase_donor(repo, pool, branch, tip, replay)
    return lifted


def _rebase_donor(repo: Path, pool: WorktreePool, branch: str, new_base: str,
                  replay: list[str]) -> None:
    """Replay the donor's non-lifted commits onto the new base and move the
    branch ref. The lifted commits are simply not replayed (they now live in the
    base). Done in a pool worktree so the caller's checkout is never disturbed."""
    head = new_base
    with pool.checkout(new_base) as wt:
        for commit in replay:
            if linearize._cherry_pick(wt, commit):
                head = gitio.rev(wt, "HEAD")
            # a commit that no longer applies is dropped (its dependency was lifted)
    gitio.update_ref(repo, f"refs/heads/{branch}", head)


def _rev_list(repo: Path, rng: str) -> list[str]:
    out = gitio.git(repo, "rev-list", "--reverse", rng)
    return [c for c in out.splitlines() if c.strip()]
