"""Frozen-candidate promotion (design §3): freeze the best merge-point that
cleared the candidate gate, stress it with the final gate against the frozen
commit (never live HEAD), and advance the target ref only on pass."""
import subprocess
import tempfile
from pathlib import Path

from . import gitio


def freeze(repo: Path, db, cfg) -> dict | None:
    """Freeze the largest non-poison merge-point that cleared the candidate
    gate. Returns {candidate, merge_point, commit} or None (nothing ready, or
    a candidate is already frozen)."""
    target = cfg.promotion["target"]
    cand_gate = cfg.promotion["candidate_gate"]
    if db.active_candidate(target):
        return None
    ladder = cfg.ladder
    best = None
    for mp in db.list_merge_points(gitio.tree_of(repo, cfg.base)):
        if not mp["result_commit"] or mp["validation_state"] == "poison":
            continue
        highest = db.highest_gate(mp["id"], mp["base_commit_sha"], ladder)
        if highest is None or ladder.index(highest) < ladder.index(cand_gate):
            continue
        if best is None or len(mp["member_patch_ids"]) > len(best["member_patch_ids"]):
            best = mp
    if best is None:
        return None
    cand_id = db.add_candidate(target, best["id"], best["result_commit"])
    gitio.update_ref(repo, f"refs/quilt/candidate/{target}",
                     best["result_commit"])
    return {"candidate": cand_id, "merge_point": best["id"],
            "commit": best["result_commit"]}


def advance(repo: Path, db, cfg) -> bool | None:
    """Run the final gate on the frozen candidate. True = promoted (target ref
    advanced), False = failed (candidate marked failed, test_fail queued),
    None = nothing frozen."""
    target = cfg.promotion["target"]
    cand = db.active_candidate(target)
    if not cand:
        return None
    mp = db.get_merge_point(cand["mp_id"])
    db.set_validation(mp["id"], "inflight")
    with tempfile.TemporaryDirectory() as wt:
        gitio.git(repo, "worktree", "add", "--detach", wt, cand["commit_sha"])
        try:
            cmd = cfg.promotion["final_cmd"].replace("{workdir}", wt)
            proc = subprocess.run(cmd, shell=True, cwd=wt,
                                  capture_output=True, text=True)
        finally:
            gitio.git(repo, "worktree", "remove", "--force", wt, check=False)
    status = "pass" if proc.returncode == 0 else "fail"
    db.record_gate(mp["id"], cfg.promotion["final_gate"],
                   mp["base_commit_sha"], status)
    if status == "pass":
        db.set_validation(mp["id"], "validated")
        gitio.update_ref(repo, f"refs/quilt/target/{target}",
                         cand["commit_sha"])
        db.set_candidate_state(cand["id"], "promoted")
        return True
    db.set_validation(mp["id"], "untested")
    db.set_candidate_state(cand["id"], "failed")
    db.enqueue_work("test_fail", mp["id"],
                    f"{cfg.promotion['final_gate']}: "
                    f"{proc.stdout[-1000:]}{proc.stderr[-1000:]}")
    return False
