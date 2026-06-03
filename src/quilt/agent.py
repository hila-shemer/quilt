"""Capable-agent skills: semantic conflict resolution (with frankenmerge glue
detection) and test-failure diagnosis. Only triaged work reaches these; the
LLM command is pluggable via [llm] in quilt.toml (design §6)."""
import subprocess
import tempfile
from pathlib import Path

from . import gitio, llm, resolve

DIAGNOSE_PROMPT = """\
A test gate failed for merge point {mp_id} (construction: {construction}).
Member tips: {tips}
Failure detail:
{detail}

Decide whether the failure is caused by the merge RESOLUTION itself (bad
conflict resolution or missing integration glue) or by one of the MEMBER
branches. Respond with ONLY a JSON object:
{{"attribution": "resolution" or "member", "culprit": "<member tip sha or empty>", "reason": "<one sentence>"}}
"""

RESOLVE_PROMPT = """\
You are resolving a git merge conflict inside this worktree.
Currently merging: {tip}
Triage estimate: {est_cause}
Conflicted files:
{files}

Edit the conflicted files to resolve every conflict semantically and remove
all conflict markers. You may conclude the merge commit yourself and add
follow-up commits for integration glue (e.g. propagating a rename to new call
sites). If you leave the merge unconcluded, your edits will be committed for
you. Do not push and do not switch branches.
"""


def _conflicted_files(wt: Path) -> list[str]:
    out = gitio.git(wt, "diff", "--name-only", "--diff-filter=U")
    return [line for line in out.splitlines() if line]


def _markers_remain(wt: Path, files: list[str]) -> bool:
    for f in files:
        p = wt / f
        if p.exists() and "<<<<<<<" in p.read_text(errors="replace"):
            return True
    return False


def resolve_conflict(repo: Path, db, cfg, item: dict) -> bool:
    """Run the capable agent on one triaged conflict work item. True (and the
    item marked done) iff a fully-resolved merge was committed and pinned."""
    cmd = cfg.llm.get("resolve_cmd")
    if not cmd:
        raise llm.LLMError("no [llm] resolve_cmd configured")
    mp = db.get_merge_point(item["target_id"])
    triage_row = db.get_triage(str(item["id"]))
    est = triage_row["est_cause"] if triage_row else "unknown"
    tips = mp["member_tips"]
    base_commit = mp["base_commit_sha"]
    with tempfile.TemporaryDirectory() as wt:
        gitio.git(repo, "worktree", "add", "--detach", wt, base_commit)
        wtp = Path(wt)
        try:
            for tip in tips:
                p = subprocess.run(["git", "-C", wt, "merge", "--no-ff", tip],
                                   capture_output=True, text=True)
                if p.returncode == 0:
                    continue
                files = _conflicted_files(wtp)
                prompt = RESOLVE_PROMPT.format(tip=tip, est_cause=est,
                                               files="\n".join(files))
                if not llm.run_edit(cmd, prompt, wtp) or _markers_remain(wtp, files):
                    return False
                if gitio.read_ref(wtp, "MERGE_HEAD"):   # agent left merge open
                    subprocess.run(["git", "-C", wt, "add", "-A"],
                                   check=True, capture_output=True)
                    subprocess.run(["git", "-C", wt, "commit", "-m",
                                    f"quilt: agent merge {tip}"],
                                   check=True, capture_output=True)
            head = gitio.rev(wtp, "HEAD")
            glue = [line for line in gitio.git(
                wtp, "rev-list", "--no-merges", head, f"^{base_commit}",
                *(f"^{t}" for t in tips)).splitlines() if line]
            construction = "frankenmerge" if glue else "agent"
            gitio.update_ref(repo, f"refs/quilt/{mp['id']}", head)
            if glue:
                fix_ref = f"refs/quilt/fix/{mp['id']}"
                gitio.update_ref(repo, fix_ref, head)
                db.add_fix(mp["id"], fix_ref, tips)
            db.upsert_merge_point(
                id=mp["id"], base_tree_sha=mp["base_tree_sha"],
                base_commit_sha=base_commit,
                member_patch_ids=mp["member_patch_ids"], member_tips=tips,
                construction=construction, result_commit=head,
                result_tree=gitio.tree_of(wtp, head))
            db.set_work_state(item["id"], "done")
            return True
        finally:
            gitio.git(repo, "worktree", "remove", "--force", wt, check=False)


def diagnose_failure(repo: Path, db, cfg, item: dict) -> dict | None:
    """Attribute one triaged test_fail item to the resolution (→ poison +
    cascade eviction) or a member branch. Returns the verdict, or None if the
    LLM output was unusable (item stays triaged)."""
    cmd = cfg.llm.get("diagnose_cmd")
    if not cmd:
        raise llm.LLMError("no [llm] diagnose_cmd configured")
    mp = db.get_merge_point(item["target_id"])
    prompt = DIAGNOSE_PROMPT.format(
        mp_id=mp["id"], construction=mp["construction"],
        tips=", ".join(mp["member_tips"]), detail=item["detail"] or "")
    try:
        verdict = llm.run_json(cmd, prompt)
        if verdict["attribution"] not in ("resolution", "member"):
            raise llm.LLMError(f"bad attribution: {verdict['attribution']!r}")
    except (llm.LLMError, KeyError):
        return None
    if verdict["attribution"] == "resolution":
        resolve.poison_merge_point(repo, db, mp["id"])
    db.set_work_state(item["id"], "done")
    return verdict
