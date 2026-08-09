"""One scheduler tick: deterministic, cheapest-first; agents only via queue."""
from itertools import combinations
from pathlib import Path

from . import gates as gates_mod
from . import gitio, probe, resolve
from .keys import merge_point_id


def _strict_subsets(ids: list[str]) -> list[frozenset[str]]:
    """All non-empty strict subsets of ids."""
    result = []
    for size in range(1, len(ids)):
        for combo in combinations(ids, size):
            result.append(frozenset(combo))
    return result


def schedulable(repo: Path, db, cfg) -> list[str]:
    """Merge-point ids a further tick would act on of its own accord.

    Empty means idle: nothing changes without new tips or drained items. The
    per-tick counters are not this predicate — `queued=0 deferred=0` is also
    what a tick prints on the pass *before* it has scheduled the heavy gates,
    so a script that waits on it stops one round too early. This looks at the
    combination set instead, which is stable between ticks.

    Read-only: enumerating combinations needs patch-ids and the base tree, and
    neither writes.
    """
    base_tree = gitio.tree_of(repo, cfg.base)
    pids = {b: gitio.patch_id(repo, cfg.base, b) for b in cfg.branches}
    last_rung = cfg.ladder[-1] if cfg.ladder else None
    out = []
    for combo in probe.enumerate_combos(cfg.branches):
        mp_id = merge_point_id(base_tree, [pids[b] for b in combo])
        mp = db.get_merge_point(mp_id)
        if mp is None:                      # never probed
            out.append(mp_id)
            continue
        if mp["validation_state"] == "poison" or db.open_work(mp_id):
            continue                        # decided, or waiting on a drain
        base = mp["base_commit_sha"]
        if not mp["result_commit"]:         # conflict with no pinned resolution
            out.append(mp_id)
            continue
        if db.failed_gate(mp_id, base, cfg.ladder):
            continue
        if db.highest_gate(mp_id, base, cfg.ladder) == last_rung:
            continue                        # cleared the whole ladder
        out.append(mp_id)
    return out


def tick(repo: Path, db, cfg, heavy_k: int = 1) -> dict:
    report = {"probed": 0, "gated": 0, "queued": 0, "deferred": 0}
    results = probe.probe_all(repo, cfg.base, cfg.branches, db)
    report["probed"] = len(results)

    # Collect the set of member_patch_ids for each untested merge point in pool.
    untested_sets: list[frozenset[str]] = []
    for r in results:
        mp = db.get_merge_point(r["id"])
        if mp and mp["validation_state"] == "untested":
            untested_sets.append(frozenset(mp["member_patch_ids"]))

    heavy_used: dict[frozenset[str], int] = {}   # untested strict subset -> heavy slots used

    for r in results:
        mp_id = r["id"]
        if r["construction"] == "conflict":
            if resolve.reusable_resolution(repo, db, mp_id) is None and \
               resolve.try_mediate(repo, db, mp_id) is None:
                report["queued"] += 1
                continue
        mp = db.get_merge_point(mp_id)
        if mp["validation_state"] == "untested" and len(mp["member_patch_ids"]) > 1:
            member_set = frozenset(mp["member_patch_ids"])
            # Find all strict subsets of this candidate that are also in the untested pool.
            untested_strict_subsets = [
                s for s in _strict_subsets(list(member_set))
                if s in untested_sets
            ]
            # Defer if any untested strict subset has exhausted its heavy slots.
            if any(heavy_used.get(s, 0) >= heavy_k for s in untested_strict_subsets):
                report["deferred"] += 1
                continue
            # Charge one slot to each untested strict subset.
            for s in untested_strict_subsets:
                heavy_used[s] = heavy_used.get(s, 0) + 1
        result = gates_mod.run_ladder(repo, db, cfg, mp_id)
        if result is not None:
            report["gated"] += 1
    return report
