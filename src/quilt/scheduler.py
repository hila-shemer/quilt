"""One scheduler tick: deterministic, cheapest-first; agents only via queue."""
from itertools import combinations
from pathlib import Path

from . import gates as gates_mod
from . import candidate, gitio, probe, resolve
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

    The short-circuit's other half. `tick` stops at the size-N set once that set
    has cleared the candidate gate, and never probes the other 2^N-2
    combinations — so none of them is schedulable, however absent they are from
    the pool. Enumerating the power set regardless would report them as pending
    forever, and `quilt idle` would never say idle.
    """
    base_tree = gitio.tree_of(repo, cfg.base)
    pids = {b: gitio.patch_id(repo, cfg.base, b) for b in cfg.branches}
    last_rung = cfg.ladder[-1] if cfg.ladder else None

    full = tuple(cfg.branches)
    cand_gate = cfg.promotion.get("candidate_gate")
    if full and cand_gate in cfg.ladder:
        full_id = merge_point_id(base_tree, [pids[b] for b in full])
        if candidate.clears_candidate_gate(db, cfg.ladder,
                                           db.get_merge_point(full_id), cand_gate):
            return []

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


def _drain(repo: Path, db, cfg, report: dict, results: list[dict],
           heavy_k: int, pool: list[dict] | None = None) -> None:
    """Resolve conflicts, apply the heavy-slot policy and run the ladder over
    *results*, in the order given.

    *pool* is what the deferral policy compares a candidate's strict subsets
    against, and defaults to *results*. The tick's second phase passes the whole
    pool while acting only on the rows it has just probed: acting on the size-N
    row twice would re-run its failed ladder and queue the same failure twice.
    """
    pool = results if pool is None else pool
    # Member sets of every untested merge-point in the pool. Heavy slots (below) are
    # charged to shared untested *subsets*, which approximates design §4's "shared
    # unvalidated resolution" serialization by member overlap — coarser than tracking
    # the resolution ref itself, but sufficient for the K-bounded heavy-test cap.
    untested_sets: set[frozenset[str]] = set()
    for r in pool:
        mp = db.get_merge_point(r["id"])
        if mp and mp["validation_state"] == "untested":
            untested_sets.add(frozenset(mp["member_patch_ids"]))

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


def tick(repo: Path, db, cfg, heavy_k: int = 1) -> dict:
    """One tick, size-N set first.

    Every other combination in the pool is a strict subset of the size-N set,
    so a full set that has cleared the candidate gate is the unique maximum
    `freeze` can choose: no subset can tie or beat it, and the other 2^N-2
    probes and their ladder runs buy nothing. That set is therefore probed and
    gated on its own first, and the rest are probed only if it did not clear the
    gate.

    Two consequences worth knowing on the green path: `probed` is 1 rather than
    2^N-1, and the full set is gated before — and so is never deferred by — the
    heavy-slot policy, which still applies to every other combination on the
    fallback path.
    """
    report = {"probed": 0, "gated": 0, "queued": 0, "deferred": 0,
              "short_circuit": False}
    combos = probe.enumerate_combos(cfg.branches)   # raises above MAX_BRANCHES
    full = tuple(cfg.branches)
    cand_gate = cfg.promotion.get("candidate_gate")

    head = probe.probe_all(repo, cfg.base, cfg.branches, db,
                           combos=[full] if full else [])
    _drain(repo, db, cfg, report, head, heavy_k)
    report["probed"] = len(head)

    if head and cand_gate in cfg.ladder and candidate.clears_candidate_gate(
            db, cfg.ladder, db.get_merge_point(head[0]["id"]), cand_gate):
        report["short_circuit"] = True
        return report

    # No short-circuit: today's pool, minus the row already handled, in the
    # order it has always been walked.
    tail = probe.probe_all(repo, cfg.base, cfg.branches, db,
                           combos=[c for c in combos if len(c) != len(full)])
    _drain(repo, db, cfg, report, tail, heavy_k, pool=head + tail)
    report["probed"] = len(head) + len(tail)
    return report
