"""One scheduler tick: deterministic, cheapest-first; agents only via queue."""
from pathlib import Path

from . import gates as gates_mod
from . import probe, resolve


def tick(repo: Path, db, cfg, heavy_k: int = 1) -> dict:
    report = {"probed": 0, "gated": 0, "queued": 0, "deferred": 0}
    results = probe.probe_all(repo, cfg.base, cfg.branches, db)
    report["probed"] = len(results)

    heavy_used: dict[str, int] = {}   # shared untested member-prefix -> heavy slots
    for r in results:
        mp_id = r["id"]
        if r["construction"] == "conflict":
            if resolve.reusable_resolution(repo, db, mp_id) is None and \
               resolve.try_mediate(repo, db, mp_id) is None:
                report["queued"] += 1
                continue
        mp = db.get_merge_point(mp_id)
        if mp["validation_state"] == "untested" and len(mp["member_patch_ids"]) > 1:
            key = ",".join(mp["member_patch_ids"][:-1])
            if heavy_used.get(key, 0) >= heavy_k:
                report["deferred"] += 1
                continue
            heavy_used[key] = heavy_used.get(key, 0) + 1
        gates_mod.run_ladder(repo, db, cfg, mp_id)
        report["gated"] += 1
    return report
