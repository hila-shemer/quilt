"""A bounded pool of detached git worktrees (Loom spec §10).

Long jobs (stress, big composition) run in parallel worktrees; the only
serialized point is the per-ref write at promotion. Worktrees are reaped on
context exit even when the body raises, so a crash never leaks a worktree.
"""
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from .. import gitio


class PoolExhausted(RuntimeError):
    """No free worktree slot within the requested timeout."""


class WorktreePool:
    def __init__(self, repo: Path, root: Path | None = None, size: int = 4):
        self.repo = Path(repo)
        self.size = size
        self.root = Path(root) if root else self.repo / ".loom-worktrees"
        self.root.mkdir(parents=True, exist_ok=True)
        self._sem = threading.Semaphore(size)

    @contextmanager
    def checkout(self, committish: str, *, timeout: float | None = None):
        """Lease a detached worktree checked out at *committish*. Blocks until a
        slot is free (or raises PoolExhausted if *timeout* elapses), and reaps
        the worktree on exit (including on exception)."""
        if not self._sem.acquire(blocking=timeout != 0, timeout=timeout):
            raise PoolExhausted(f"no worktree slot (size={self.size})")
        wt = self.root / f"wt-{uuid.uuid4().hex[:12]}"
        gitio.git(self.repo, "worktree", "add", "--detach", str(wt), committish)
        try:
            yield wt
        finally:
            gitio.git(self.repo, "worktree", "remove", "--force", str(wt), check=False)
            self._sem.release()
