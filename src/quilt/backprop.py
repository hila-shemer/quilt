"""Frankenmerge back-prop (design §8): glue commits are an obligation owed to
member branches. offer() exports them as patch files (pending → offered);
check_adopted() detects, by stable patch-id, that a member branch has picked
them up (offered → adopted)."""
from pathlib import Path

from . import gitio


def _glue_commits(repo: Path, db, fix: dict) -> list[str]:
    """Non-merge commits reachable from the fix ref but from no member tip."""
    mp = db.get_merge_point(fix["merge_point_id"])
    excludes = [f"^{t}" for t in fix["affected_tips"]]
    out = gitio.git(repo, "rev-list", "--no-merges", fix["patch_ref"],
                    f"^{mp['base_commit_sha']}", *excludes)
    return [line for line in out.splitlines() if line]


def offer(repo: Path, db, outdir: Path) -> list[Path]:
    """Export each pending fix's glue commits as .patch files; mark offered."""
    written = []
    for fix in db.list_fixes(state="pending"):
        outdir.mkdir(parents=True, exist_ok=True)
        for sha in _glue_commits(repo, db, fix):
            out = gitio.git(repo, "format-patch", "-1", sha, "-o", str(outdir))
            written.extend(Path(line) for line in out.splitlines() if line)
        db.set_fix_state(fix["id"], "offered")
    return written


def check_adopted(repo: Path, db, cfg) -> list[int]:
    """Mark offered fixes adopted when every glue patch-id appears in some
    member branch's base..tip range."""
    offered = db.list_fixes(state="offered")
    if not offered:
        return []
    member_pids: set[str] = set()
    for b in cfg.branches:
        member_pids |= gitio.patch_ids_of_range(repo, cfg.base, b)
    adopted = []
    for fix in offered:
        glue_pids = {gitio.commit_patch_id(repo, sha)
                     for sha in _glue_commits(repo, db, fix)}
        if glue_pids and glue_pids <= member_pids:
            db.set_fix_state(fix["id"], "adopted")
            adopted.append(fix["id"])
    return adopted
