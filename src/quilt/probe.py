"""Enumerate branch combinations (N<=5) and probe each with merge-tree.
Sequential pairwise merges; conflict at any step marks the combo 'conflict'."""
from itertools import combinations
from pathlib import Path

from . import gitio
from .keys import merge_point_id

MAX_BRANCHES = 5


def enumerate_combos(branches: list[str]) -> list[tuple[str, ...]]:
    if len(branches) > MAX_BRANCHES:
        raise ValueError(f"more than {MAX_BRANCHES} branches; promote one first")
    out = []
    for k in range(1, len(branches) + 1):
        out.extend(combinations(branches, k))
    return out


def probe_combo(repo: Path, base: str, branches: tuple[str, ...]):
    """Merge branches into base sequentially via merge-tree; returns
    (construction, result_commit, result_tree)."""
    current = gitio.rev(repo, base)
    for branch in sorted(branches, key=lambda b: gitio.patch_id(repo, base, b)):
        res = gitio.merge_tree(repo, current, branch)
        if not res.clean:
            return "conflict", None, None
        current = gitio.commit_tree(repo, res.tree,
                                    parents=[current, gitio.rev(repo, branch)],
                                    msg=f"quilt: merge {branch}")
    return "clean", current, gitio.tree_of(repo, current)


def probe_all(repo: Path, base: str, branches: list[str], db) -> list[dict]:
    base_tree = gitio.tree_of(repo, base)
    base_commit = gitio.rev(repo, base)
    pids = {b: gitio.patch_id(repo, base, b) for b in branches}
    results = []
    for combo in enumerate_combos(branches):
        member_pids = [pids[b] for b in combo]
        mp_id = merge_point_id(base_tree, member_pids)
        construction, commit, tree = probe_combo(repo, base, combo)
        db.upsert_merge_point(
            id=mp_id, base_tree_sha=base_tree, base_commit_sha=base_commit,
            member_patch_ids=member_pids,
            member_tips=[gitio.rev(repo, b) for b in combo],
            construction=construction, result_commit=commit, result_tree=tree)
        if commit:
            gitio.update_ref(repo, f"refs/quilt/{mp_id}", commit)
        results.append({"id": mp_id, "branches": list(combo),
                        "construction": construction})
    return results
