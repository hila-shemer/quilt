"""Resolution layer: pinned refs + git-mediate for trivial conflicts.
Reuse is blocked only by validation_state == 'poison'."""
import subprocess
import tempfile
from pathlib import Path

from . import gitio


def evict(repo: Path, db, mp_ids: list[str]) -> None:
    """Delete refs/quilt/<id> for each cascade-reset merge-point id.
    Ignores missing refs."""
    import subprocess
    for mp_id in mp_ids:
        subprocess.run(
            ["git", "-C", str(repo), "update-ref", "-d", f"refs/quilt/{mp_id}"],
            capture_output=True,
        )


def reusable_resolution(repo: Path, db, mp_id: str) -> str | None:
    mp = db.get_merge_point(mp_id)
    if mp is None or mp["validation_state"] == "poison":
        return None
    return gitio.read_ref(repo, f"refs/quilt/{mp_id}")


def try_mediate(repo: Path, db, mp_id: str) -> str | None:
    """Try resolving a conflicted merge-point with git-mediate in a temp
    worktree. Returns merge commit SHA, or None (queues agent work)."""
    mp = db.get_merge_point(mp_id)
    tips = mp["member_tips"]
    base_commit = mp["base_commit_sha"]
    with tempfile.TemporaryDirectory() as wt:
        gitio.git(repo, "worktree", "add", "--detach", wt, base_commit)
        try:
            wt_path = Path(wt)
            for tip in tips:
                p = subprocess.run(["git", "-C", wt, "merge", "--no-ff", tip],
                                   capture_output=True, text=True)
                if p.returncode == 0:
                    continue
                m = subprocess.run(["git-mediate"], cwd=wt,
                                   capture_output=True, text=True)
                if m.returncode != 0:
                    db.enqueue_work("conflict", mp_id, (m.stdout + m.stderr)[-2000:])
                    return None
                subprocess.run(["git", "-C", wt, "commit", "-am",
                                f"quilt: mediated merge {tip}"],
                               check=True, capture_output=True)
            merged = gitio.rev(wt_path, "HEAD")
            gitio.update_ref(repo, f"refs/quilt/{mp_id}", merged)
            db.upsert_merge_point(
                id=mp_id, base_tree_sha=mp["base_tree_sha"],
                base_commit_sha=mp["base_commit_sha"],
                member_patch_ids=mp["member_patch_ids"],
                member_tips=tips, construction="mediated",
                result_commit=merged, result_tree=gitio.tree_of(repo, merged))
            return merged
        finally:
            gitio.git(repo, "worktree", "remove", "--force", wt, check=False)
