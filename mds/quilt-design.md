# Quilt Integration Pipeline — Design

An agent-inhabited integration pipeline modeled on the kernel's subsystem-tree →
linux-next → mainline flow, with a content-keyed database of merge-points, a
validated-resolution layer over plain git refs, a monotone test-gate ladder, and
automatic promotion of stress-passed candidates to `main`.

Quilt is a standalone tool. It operates on repos like `singlehex` and its
subcomponents (e2/, ml_lib/, …), which serve as the test corpus.

## Load-bearing invariant

The expensive LLM is **off the critical path for the happy path.** A merge-point
that merges cleanly and clears the fast gates reaches long-test launch with *zero*
expensive LLM calls. The LLM is demand-summoned only for (a) genuine semantic
conflicts and (b) test failures, and even then a cheap triage model runs first to
decide whether the capable agent is worth invoking. Everything deterministic runs
ahead of everything probabilistic.

Two corollaries drive the whole design:

- **Determinism front-runs.** `merge-tree`, `git-mediate`, validated-resolution
  reuse, compiles, and fast tests are all deterministic and run first. Long
  tests start speculatively on the good path before any agent is woken.
- **The DB is the source of truth, not git.** Git stores trees and refs; the DB
  stores *what we know about combinations* — merge result, gate level reached,
  and whether the combination's resolution is trusted. Everything is keyed by
  content (tree + patch-ids), so base movement invalidates correctly rather
  than lying.

---

## 1. Data model

Keyed by content, never by branch name or commit SHA. A merge-point's identity is

```
id = hash(base_tree_sha, sorted(member_patch_ids))
```

- `base_tree_sha` — the **tree** (not commit) of the base. Base commits that
  don't change content keep all merge-points cache-hot.
- `member_patch_ids` — `git patch-id --stable` per member branch (one
  mostly-1-commit feature branch each). Rebases and metadata edits (committer,
  message, date) don't change the patch-id, so they don't invalidate.

Resolution caching is **per merge-point** (whole merge), not per conflict hunk:
a resolved merge is committed and pinned under `refs/quilt/<id>`. Reuse is a
ref lookup. There is no rerere and no per-hunk preimage table; with N ≤ 5
members the cross-merge reuse rerere would buy is negligible, and merge-level
caching means a poison verdict invalidates exactly one merge-point plus its
supersets.

```sql
-- A materialized combination of feature tips against a base.
CREATE TABLE merge_point (
  id               TEXT PRIMARY KEY,    -- hash(base_tree_sha, sorted patch-ids)
  base_tree_sha    TEXT NOT NULL,
  base_commit_sha  TEXT NOT NULL,       -- convenience for git ops
  member_patch_ids TEXT NOT NULL,       -- sorted, canonical-joined
  member_tips      TEXT NOT NULL,       -- commit SHAs at registration
  result_commit    TEXT,                -- merge commit (refs/quilt/<id>)
  result_tree      TEXT,
  construction     TEXT NOT NULL,       -- clean | mediated | agent | frankenmerge | dropped
  validation_state TEXT NOT NULL,       -- untested | inflight | validated | poison
  created_at       INTEGER NOT NULL
);

-- Monotone gate ladder. Staleness = absence of a row for the current base:
-- base movement changes base_commit_sha, so old rows stop matching.
CREATE TABLE gate_status (
  merge_point_id  TEXT NOT NULL REFERENCES merge_point(id),
  gate            TEXT NOT NULL,        -- compiles | triton | mllib_ut | t4h | t4day
  base_commit_sha TEXT NOT NULL,
  status          TEXT NOT NULL,        -- inflight | pass | fail
  result_ref      TEXT,                 -- logs / artifacts
  started_at      INTEGER,
  finished_at     INTEGER,
  PRIMARY KEY (merge_point_id, gate, base_commit_sha)
);

-- Agent-authored integration glue with no feature-branch home (the
-- back-propagation obligation). Tracks whether it's been offered upstream.
CREATE TABLE frankenmerge_fix (
  merge_point_id TEXT NOT NULL REFERENCES merge_point(id),
  patch_ref      TEXT NOT NULL,
  affected_tips  TEXT NOT NULL,
  backprop_state TEXT NOT NULL          -- pending | offered | adopted | abandoned
);

-- Cheap fast-model estimate used purely for scheduling/routing.
CREATE TABLE triage (
  id           TEXT PRIMARY KEY,
  target_id    TEXT NOT NULL,           -- merge_point.id or gate_status rowid
  kind         TEXT NOT NULL,           -- conflict | test_fail
  est_cause    TEXT,
  effort_class TEXT NOT NULL,           -- trivial | moderate | complex
  model        TEXT,
  created_at   INTEGER NOT NULL
);
```

The gate ladder is monotone: `compiles ⊂ triton ⊂ mllib_ut ⊂ t4h ⊂ t4day`.
The highest rung cleared is **derived** from `gate_status` rows, never stored.
Each target line declares its required rung: `next` needs fast gates,
`local-stable` needs `t4h`, `main` needs `t4day`.

---

## 2. Conflict & resolution lifecycle

Cheapest-first escalation. Steps 1–3 are deterministic; the LLM only enters at 4.

1. **Detect** — `git merge-tree --write-tree` (no checkout, no working tree,
   parallelizable across all candidate combinations). Clean → go to gates.
   Conflict → step 2.
2. **Reuse cached resolution** — same `id` ⇒ same base tree and same member
   patch-ids, so a previously resolved merge under `refs/quilt/<id>` is valid
   verbatim. Reuse for testing requires `validation_state ∈ {untested,
   validated}` — `poison` blocks reuse.
3. **Auto-trivial** — run `git-mediate` on remaining conflicts to clear the ones
   that aren't actually semantic (one side subsumes the other relative to base).
4. **Agent** — only genuine semantic conflicts reach here, and only after the
   triage model (§5) has classified effort. The agent either resolves (recording
   the merge as `agent`/`frankenmerge` + opening a `frankenmerge_fix` row) or
   declares the combination non-viable (`construction = dropped`).

**Validation and poison.** A merge-point becomes `validated` when it passes a
long test. It becomes `poison` when a long-test failure is traced to its
resolution. Poison cascades to every merge-point whose member set is a superset
— those drop back to `untested` and re-resolve.

---

## 3. Gate ladder & test caching

- Each gate runner is a deterministic shell command writing `pass`/`fail` plus
  `base_commit_sha` into `gate_status`.
- **Lazy invalidation.** On base movement, do not eagerly re-test; old rows stop
  matching the current base. Recompute on demand when a point is actually
  needed for promotion. Expensive-test cost is bounded by what you promote, not
  by churn.
- **Cheap probe eager, expensive lazy.** The `merge-tree` layer is re-probed for
  the surviving pool in parallel on every base move; long tests stay lazy.
- **Frozen candidate tag for stress.** `t4day` pins to a frozen `main-candidate`
  tag, never live HEAD. `main` advances only when a frozen tag clears `t4day`.
  In steady state most promotion is `t4h` → `local-stable`.

---

## 4. Scheduler

**Happy path (no LLM):** enumerate candidates → `merge-tree` clean → fast gates
pass → launch long tests immediately. No agent woken.

**The all-pass short-circuit.** The happy path enumerates the full set first,
gated before any other candidate is probed. Every other combination is a strict
subset of it, and promotion picks the largest merge-point that cleared the
candidate gate, so a full set that cleared that gate is the unique winner and
enumerating the rest buys nothing. On a green run the pool is 1 merge-point, not
2^N−1.

The bar is `freeze`'s — the candidate gate on the full ladder — and never a
clean merge: a set that merges clean and fails `tests` has not cleared a
candidate gate of `tests`. When the full set does not clear it, the tick probes
the rest of the power set exactly as before, so the subset rows the poison
cascade resets supersets over and the failure bisect compares are all still
there, and then walks them by *descending* size, stopping at the first set that
clears the candidate gate. The `schedulable` predicate carries the same rule, or
`idle` would count the un-probed subsets as pending forever.

**Amended 2026-10-27 — the fallback descends by size.** Enumeration stays
exhaustive and every combination is still probed; what the amendment changes is
the order the *ladder* is spent in, and where the walk stops. The answer is the
largest set that clears the candidate gate, so a set of size *k* that clears it
beats every smaller one and none of them has to run: the fallback walks
largest-first and stops at the first set that clears the gate, maximal because
everything above it was tried and did not. A suite is the unit of cost, so this
is the difference between one suite per conflict-free subset of the offer —
`2^N−1`, less `2^(N−2)` when one pair of series is mutually exclusive — and one
per set at the size that answers. Measured on a four-series stack with one
incompatible pair, 2026-10-27: **11 suites (~53 min at 287 s) before, 1 (~5 min)
after**; solomon's own press of that stack paid the same 11 suites and was cut
at its 50-minute cap with nothing landed. Two consequences: the heavy-slot
policy is off while a candidate gate is set to stop on (largest-first, a
candidate's strict subsets are the *smaller* sets the walk has not reached yet,
so deferring on them starves the walk — it re-runs the same failing suite every
tick and settles for a smaller set than one that would have passed), and
`schedulable` walks the same order and breaks at the same predicate.

**Routing on break:** any conflict (after mediate) or gate failure triggers the
cheap triage model first. `trivial`/`moderate` → wake the capable agent.
`complex` → queue, deprioritize, or drop.

**Shared-unvalidated-resolution serialization.** When several ready merge-points
share a member subset whose resolution is `untested`/`inflight`, allow at most
*K* concurrent heavy slots over that resolution; spend one slot, learn the
verdict, then release or drop the dependents.

**Materialization policy.** Power set of N ≤ 5 branches (≤ 31 combinations),
of which the size-N set is probed first and the rest only when it has not
cleared the candidate gate (see *the all-pass short-circuit* above).
N shrinks by promoting "proven" branches — branch surviving merge with all
others promotes alone to base/next, reducing N. At N = 5 new branches are
rejected. An optional `staging` line before `next` adds budget headroom.

---

## 5. Triage model

A small, time-boxed model that, given a conflict diff or test-failure log,
returns `{est_cause, effort_class ∈ {trivial, moderate, complex}}`. It only
routes; it never fixes. A `trivial` verdict (e.g. one-token rename collision)
can be auto-applied by a deterministic fixer without the capable agent.

---

## 6. Skills (LLM) vs scripts (deterministic)

| Subtask | Mechanism | Model tier |
|---|---|---|
| `merge-tree` probe | shell | — |
| `git-mediate` invoke + parse | shell | — |
| resolution ref reuse | shell | — |
| DB CRUD + content keying | shell | — |
| gate runners | shell | — |
| staleness + poison cascade | shell | — |
| promotion (frozen tag → main on `t4day`) | shell | — |
| triage estimate | skill | small / fast |
| semantic conflict resolution + frankenmerge | skill | capable |
| branch → features split | skill | capable |
| test-failure diagnosis | skill | capable |
| back-prop patch authoring | skill | capable |
| promotion-readiness judgment | skill | capable / human |

---

## 7. Phased build

- **Phase 0 — Schema + merge-probe core.** Content-keyed DB, patch-id keying,
  power-set enumeration (exhaustive — the tick narrows what it *probes*, not
  what is enumerated), parallel `merge-tree` probe. Deliverable: map of which
  tips merge cleanly. No tests, no LLM.
- **Phase 1 — Gate ladder + lazy invalidation.** Configurable gate runners →
  `gate_status` keyed by base; staleness-by-absence; promotion readiness.
- **Phase 2 — Resolution cache + validation tagging.** `refs/quilt/*` pinning,
  reuse-unless-poison, mediate integration, poison cascade.
- **Phase 3 — Scheduler.** Happy-path: probe → fast gates → long-test launch;
  routing & K-bounded heavy-slot serialization; agent work queue.
- **Phase 4 — Capable skills.** semantic-conflict-resolve / frankenmerge,
  branch-split, test-failure-diagnose, back-prop authoring; triage skill.
- **Phase 5 — Promotion automation + back-prop loop.** Frozen-tag stress runs;
  auto-promotion to `local-stable` / `main`; frankenmerge obligations driven to
  offered/adopted.

## 8. Open problems

- **Failure → resolution attribution.** Tracing a long-test failure to a member
  or resolution may itself need the agent (or a bisect over merge-points —
  power-set structure makes this tractable: compare gate results of subsets).
- **Frankenmerge back-prop adoption.** The tool can offer the patch; adoption is
  a process problem.
