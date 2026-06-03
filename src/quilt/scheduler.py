"""One scheduler tick: deterministic, cheapest-first; agents only via queue."""
from itertools import combinations
from pathlib import Path

from . import gates as gates_mod
from . import probe, resolve


def _strict_subsets(ids: list[str]) -> list[frozenset[str]]:
    """All non-empty strict subsets of ids."""
    result = []
    for size in range(1, len(ids)):
        for combo in combinations(ids, size):
            result.append(frozenset(combo))
    return result


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
