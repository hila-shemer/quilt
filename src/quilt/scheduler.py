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


def _walk_order(branches: list[str]) -> list[tuple[str, ...]]:
    """The offer in the order a tick walks it: the full set first, then every
    other combination largest-first, and inside one size the order
    `enumerate_combos` gives — the branches' own order, position by position.

    Size is descending because the answer the tick is looking for is the
    *largest* set that clears the candidate gate: `freeze` picks the largest
    ready merge-point, so a size-k set that clears the gate beats every smaller
    one and none of them has to run. Walking up — the order `enumerate_combos`
    returns, and the order the fallback used to drain — spends a suite on every
    conflict-free subset of the offer below the answer: 2^N-2 of them when
    nothing conflicts, and 2^N-1-2^(N-2) when one pair of series is mutually
    exclusive (the sets holding both conflict, so they queue instead).

    This is a *drain* order and not an enumeration. `enumerate_combos` and
    `probe_all` keep their whole-power-set contract (probe.py), every
    combination is still probed and still gets its row, and the poison cascade
    and the failure bisect still find the subsets they reset and compare. What
    the order decides is which combinations the ladder is spent on.
    """
    full = tuple(branches)
    rest = [c for c in probe.enumerate_combos(branches) if len(c) != len(full)]
    return ([full] if full else []) + sorted(rest, key=len, reverse=True)


def schedulable(repo: Path, db, cfg) -> list[str]:
    """Merge-point ids a further tick would act on of its own accord.

    Empty means idle: nothing changes without new tips or drained items. The
    per-tick counters are not this predicate — `queued=0 deferred=0` is also
    what a tick prints on the pass *before* it has scheduled the heavy gates,
    so a script that waits on it stops one round too early. This looks at the
    combination set instead, which is stable between ticks.

    Read-only: enumerating combinations needs patch-ids and the base tree, and
    neither writes.

    The walk's other half, twice over. `tick` probes the size-N set on its own
    first and stops when it clears the candidate gate, and on the fallback it
    walks largest-first and stops at the first set that does; both leave
    combinations below the stop unprobed and ungated. Re-enumerating the power
    set regardless would report them as pending forever and `quilt idle` would
    never say idle — so this walks the same order, breaks at the same
    predicate, and the two cannot disagree about which combinations a further
    tick would reach."""
    base_tree = gitio.tree_of(repo, cfg.base)
    pids = {b: gitio.patch_id(repo, cfg.base, b) for b in cfg.branches}
    last_rung = cfg.ladder[-1] if cfg.ladder else None
    cand_gate = cfg.promotion.get("candidate_gate")
    can_stop = cand_gate in cfg.ladder

    out = []
    for combo in _walk_order(cfg.branches):
        mp_id = merge_point_id(base_tree, [pids[b] for b in combo])
        mp = db.get_merge_point(mp_id)
        if mp is None:                      # never probed
            out.append(mp_id)
            continue
        if mp["validation_state"] == "poison" or db.open_work(mp_id):
            continue                        # decided, or waiting on a drain
        # The tick's stop, read off the same predicate the tick reads: a set
        # that has cleared the candidate gate is the answer, every set above it
        # in this order has been tried, and nothing below it is reached.
        if can_stop and candidate.clears_candidate_gate(
                db, cfg.ladder, mp, cand_gate):
            return out
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
           heavy_k: int, pool: list[dict] | None = None,
           stop_gate: str | None = None) -> None:
    """Resolve conflicts, apply the heavy-slot policy and run the ladder over
    *results*, in the order given.

    *pool* is what the deferral policy compares a candidate's strict subsets
    against, and defaults to *results*. The tick's second phase passes the whole
    pool while acting only on the rows it has just probed: acting on the size-N
    row twice would re-run its failed ladder and queue the same failure twice.

    *stop_gate* is the candidate gate, and it ends the walk. A row that has
    cleared it is the largest set that can be promoted, so every combination
    below it is a suite spent on a set `freeze` could not prefer — the caller
    hands *results* over in descending-size order, and everything the walk
    passed before the stop was tried and did not clear the gate. That is what
    makes the set it stops on maximal, exactly as the size-N short-circuit is
    made maximal by every other set being its subset. None means the walk runs
    to the end of *results*, which is what a config with no candidate gate gets:
    with no gate there is nothing to stop on.

    A *stop_gate* also turns the heavy-slot policy off, because the two are
    scheduling for opposite walks. The policy defers a candidate whose strict
    subsets are untested — a set's own subsets before the set — which is the
    right order for a walk that means to test all of them and the wrong one for
    a walk that means to stop at the first that clears the gate: largest-first,
    a candidate's strict subsets are the *smaller* sets the walk has not reached
    yet, so every candidate above size 1 is deferred by sets that are untested
    only because the walk is about to skip them. Measured on the four-series
    stack, that is not merely suites spent — the walk starves, re-runs the same
    failing suite on every tick, and settles for a smaller set than one that
    would have passed. With no gate to stop on there is no such trade, and the
    policy stands.
    """
    pool = results if pool is None else pool
    # Member sets of every untested merge-point in the pool. Heavy slots (below) are
    # charged to shared untested *subsets*, which approximates design §4's "shared
    # unvalidated resolution" serialization by member overlap — coarser than tracking
    # the resolution ref itself, but sufficient for the K-bounded heavy-test cap.
    untested_sets: set[frozenset[str]] = set()
    if stop_gate is None:
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
        if stop_gate is None and mp["validation_state"] == "untested" \
                and len(mp["member_patch_ids"]) > 1:
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
        if stop_gate is not None and candidate.clears_candidate_gate(
                db, cfg.ladder, db.get_merge_point(mp_id), stop_gate):
            break


def tick(repo: Path, db, cfg, heavy_k: int = 1) -> dict:
    """One tick, largest set first.

    The size-N set is probed and gated on its own first: every other
    combination is a strict subset of it, so a full set that has cleared the
    candidate gate is the unique maximum `freeze` can choose — no subset can
    tie or beat it, and the other 2^N-2 probes and their ladder runs buy
    nothing.

    When it does not clear the gate — a conflict, or a rung it fails — the
    tick walks the rest by descending size and stops at the first combination
    that clears it, which is maximal for the same reason: every combination
    above it has been tried and did not. So the fallback costs one suite per
    combination at the size that answers, plus the ones above it that got past
    their own gates, where the ascending walk spent a suite on every
    conflict-free subset of the offer. The green path is untouched: probed is 1
    rather than 2^N-1, and the full set is gated before — and so is never
    deferred by — the heavy-slot policy, which still applies to every row the
    walk reaches.
    """
    report = {"probed": 0, "gated": 0, "queued": 0, "deferred": 0,
              "short_circuit": False}
    walk = _walk_order(cfg.branches)        # raises above MAX_BRANCHES
    full = tuple(cfg.branches)
    cand_gate = cfg.promotion.get("candidate_gate")
    stop_gate = cand_gate if cand_gate in cfg.ladder else None

    head = probe.probe_all(repo, cfg.base, cfg.branches, db,
                           combos=[full] if full else [])
    _drain(repo, db, cfg, report, head, heavy_k)
    report["probed"] = len(head)

    if stop_gate is not None and head and candidate.clears_candidate_gate(
            db, cfg.ladder, db.get_merge_point(head[0]["id"]), stop_gate):
        report["short_circuit"] = True
        return report

    # No short-circuit: the rest of the offer, largest first, stopping at the
    # first combination that clears the gate.
    tail = probe.probe_all(repo, cfg.base, cfg.branches, db,
                           combos=walk[1:])
    _drain(repo, db, cfg, report, tail, heavy_k, pool=head + tail,
           stop_gate=stop_gate)
    report["probed"] = len(head) + len(tail)
    return report
