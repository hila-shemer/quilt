"""Enumerate branch combinations (N<=5) and probe each with merge-tree.
Sequential pairwise merges; conflict at any step marks the combo 'conflict'.

Enumeration is exhaustive by contract: the whole power set is what
`enumerate_combos` returns and what `probe_all` probes when it is not told
otherwise, so that every combination has a row for the poison cascade, the
failure bisect and `freeze` to read.

What a caller decides is which combinations are *probed* and which the ladder is
*spent* on, and those are not the same question. A tick probes the size-N set on
its own first and stops there when it has cleared the candidate gate, before the
other 2^N-2 are probed at all; when it has not, it probes the rest and walks them
largest-first, stopping at the first set that clears the gate (see
`scheduler.tick`). Probing a combination is a merge; running the ladder over one
is a suite, and the walk order is what decides how many sets pay that."""
from itertools import combinations
from pathlib import Path

from . import gitio, resolve
from .keys import merge_point_id

MAX_BRANCHES = 5


def enumerate_combos(branches: list[str]) -> list[tuple[str, ...]]:
    if len(branches) > MAX_BRANCHES:
        raise ValueError(f"more than {MAX_BRANCHES} branches; promote one first")
    out = []
    for k in range(1, len(branches) + 1):
        out.extend(combinations(branches, k))
    return out


def probe_combo(repo: Path, base: str, branches: tuple[str, ...],
                pids: dict[str, str]):
    """Merge branches into base sequentially via merge-tree; returns
    (construction, result_commit, result_tree).

    *pids* is a pre-computed {branch: patch_id} dict (from probe_all) used
    to sort the merge order without re-invoking git patch-id."""
    current = gitio.rev(repo, base)
    for branch in sorted(branches, key=pids.__getitem__):
        res = gitio.merge_tree(repo, current, branch)
        if not res.clean:
            return "conflict", None, None
        current = gitio.commit_tree(repo, res.tree,
                                    parents=[current, gitio.rev(repo, branch)],
                                    msg=f"quilt: merge {branch}")
    return "clean", current, gitio.tree_of(repo, current)


def probe_all(repo: Path, base: str, branches: list[str], db,
              combos: list[tuple[str, ...]] | None = None) -> list[dict]:
    """Probe *combos* of *branches* with merge-tree and record each merge-point.

    *combos* defaults to the whole power set (`enumerate_combos`). A caller may
    pass a narrower list — the tick passes the size-N set alone first, and the
    rest only if that set did not clear the candidate gate. Results come back in
    the order they were given.
    """
    base_tree = gitio.tree_of(repo, base)
    base_commit = gitio.rev(repo, base)
    pids = {}
    for b in branches:
        try:
            pids[b] = gitio.patch_id(repo, base, b)
        except gitio.GitError as e:
            # patch_id raises for an empty diff *and* for a bad ref / missing object;
            # carry the underlying cause rather than asserting "empty diff".
            raise ValueError(f"branch {b}: {e}") from e
    results = []
    for combo in (enumerate_combos(branches) if combos is None else combos):
        member_pids = [pids[b] for b in combo]
        mp_id = merge_point_id(base_tree, member_pids)
        # Skip upsert+merge if a pinned, non-poison resolution already exists.
        existing_ref = resolve.reusable_resolution(repo, db, mp_id)
        if existing_ref is not None:
            existing_mp = db.get_merge_point(mp_id)
            results.append({"id": mp_id, "branches": list(combo),
                            "construction": existing_mp["construction"]})
            continue
        construction, commit, tree = probe_combo(repo, base, combo, pids)
        db.upsert_merge_point(
            id=mp_id, base_tree_sha=base_tree, base_commit_sha=base_commit,
            member_patch_ids=member_pids,
            member_tips=[gitio.rev(repo, b) for b in combo],
            member_branches=list(combo),
            construction=construction, result_commit=commit, result_tree=tree)
        if commit:
            gitio.update_ref(repo, f"refs/quilt/{mp_id}", commit)
        results.append({"id": mp_id, "branches": list(combo),
                        "construction": construction})
    return results
