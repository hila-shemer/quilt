"""Single-branch end-to-end driver (Loom spec §12 step 4) — no LLM on the clean path.

Wires the first fully-deterministic, no-agent slice:

  harvest (§6.3) → linearize.solve (P2) → staging → promote.run (P3) → next_staging

`harvest` lifts test-only commits to the base; the remaining branch commits become
single-commit increments the linearizer materializes into a maximal-green
`staging`; `promote` stress-validates a milestone and fast-forwards `next_staging`.
On a clean branch this makes zero `decide()`/LLM calls (the auditor's
deterministic checks suffice).
"""
from pathlib import Path

from .. import gitio
from . import harvest, increments, linearize, promote
from .increments import Increment
from .worktree import WorktreePool


def run(repo: Path, db, cfg, branch: str,
        test_globs=harvest.DEFAULT_TEST_GLOBS,
        pool: WorktreePool | None = None) -> dict:
    """Drive one injected branch end-to-end. Returns
    {lifted, increments, solution, promotion}."""
    pool = pool or WorktreePool(repo, size=4)

    lifted = harvest.run(repo, db, cfg, branch, test_globs, pool)

    base_sha = gitio.rev(repo, cfg.base)
    remaining = [c for c in gitio.git(repo, "rev-list", "--reverse",
                                      f"{base_sha}..{branch}").splitlines() if c.strip()]
    incs = []
    for i, sha in enumerate(remaining):
        inc = Increment(id=f"{branch}-{i}-{sha[:8]}", patches={"self": sha},
                        priority_class="feature", age=i)
        increments.add(db, inc)
        incs.append(inc)

    sol = linearize.solve(repo, db, cfg, incs, pool)
    promo = promote.run(repo, db, cfg, pool)
    return {"lifted": lifted, "increments": incs, "solution": sol, "promotion": promo}
