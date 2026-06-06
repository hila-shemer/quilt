# Loom P2 — Linearizer + Seam-Search (spec §6.2, §4.3, §10)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Steps use `- [ ]` checkboxes. This is the **largest build**; ship it task-by-task,
> green at each commit.

**Goal:** Turn a viable combination (from quilt's probe) into an **all-commits-green,
tests-first, bisectable** series on `staging` (`refs/loom/staging`, local, never pushed).
The increment-set is the source of truth; `staging` is `materialize(sort(set, DAG, policy))`,
recomputed and force-pushed (locally) when the set changes (principle §2.1).

**Architecture:** Deterministic ordering + per-commit gating + maximal-green-prefix
truncation, with a single Haiku decision hook at each red seam to classify *hard-dependency*
(→ reorder, non-destructive) vs *incidental-conflict* (→ repair, debt). Adds the per-commit
gate cache (§4.3), the `git worktree` pool, and the reflow epoch (§10).

**Depends on:** P1 (auditor gates every per-commit green). **Consumers:** P3, P4, P7.

**Tech stack:** Python 3.12 stdlib, pytest. Reuses `quilt.gitio` (rev/tree_of/commit_tree/
patch_id/update_ref/read_ref), `quilt.db`, `quilt.gates`, `quilt.llm`, `quilt.loom.audit`.

---

## File structure

```
src/quilt/loom/
  schema.py            # extend: increment, dep_edge, commit_gate tables
  worktree.py          # NEW: detached-worktree pool (§10)
  increments.py        # NEW: increment CRUD + ordering key
  commitcache.py       # NEW: per-commit gate cache (§4.3) + per-commit gate runner (calls P1 audit)
  linearize.py         # NEW: materialize + maximal-green-prefix + seam detection + reorder/repair routing
  epoch.py             # NEW: reflow epoch guard (§10)
tests/loom/
  test_worktree.py  test_increments.py  test_commitcache.py
  test_linearize.py  test_epoch.py
```

---

## Task 1: Worktree pool (§10)

**Files:** `worktree.py`, `tests/loom/test_worktree.py`

Spec §10: a job scheduler over a `git worktree` pool. Long jobs run in parallel worktrees;
the only serialized point is the per-ref write at promotion. Idempotent, tree-hash-keyed,
crash-resumable.

```python
class WorktreePool:
    def __init__(self, repo: Path, root: Path, size: int = 4): ...
    @contextmanager
    def checkout(self, committish: str) -> Iterator[Path]:
        """Lease a detached worktree at committish; reaped (worktree remove --force) on exit."""
```

- [ ] **Step 1: Failing tests** — leasing yields a path whose `HEAD` tree == `tree_of(committish)`;
  pool blocks/raises when `size` exhausted; a crash (exception in the `with`) still reaps the
  worktree (no leaked `git worktree list` entries).
- [ ] **Step 2: Implement** over `gitio.git(repo, "worktree", "add"/"remove", ...)`, mirroring
  the temp-worktree pattern already in `quilt.gates.run_ladder`/`quilt.candidate`. Bound
  concurrency with a `threading.Semaphore(size)`.
- [ ] **Step 3:** Tests PASS. **Commit:** `feat(loom): git worktree pool`

---

## Task 2: Increment store + ordering key (spec §4.1, §6.2)

**Files:** `schema.py` (extend), `increments.py`, `tests/loom/test_increments.py`

- [ ] **Step 1:** Extend `schema.py` (additive) with `increment` and `dep_edge`:

```sql
CREATE TABLE IF NOT EXISTS increment (
  id            TEXT PRIMARY KEY,
  tier_target   TEXT NOT NULL,                 -- zoo | staging
  patches       TEXT NOT NULL,                 -- json {repo: patch_ref}
  priority_class TEXT NOT NULL,                -- test|invariant|fix|feature|rewrite
  deps          TEXT NOT NULL DEFAULT '[]',    -- json [increment_id]  (HINT only)
  dod           TEXT,                          -- json {required_test_sets}  (zoo)
  base          TEXT NOT NULL,                 -- next@<sha>
  oracle_ref    TEXT,
  status        TEXT NOT NULL DEFAULT 'building', -- green|red|building|parked
  stability     REAL NOT NULL DEFAULT 0,       -- churn metric
  patch_id      TEXT NOT NULL,                 -- git patch-id --stable (cache identity)
  age           INTEGER NOT NULL               -- insertion order / timestamp
);
CREATE TABLE IF NOT EXISTS dep_edge (
  x TEXT NOT NULL, y TEXT NOT NULL,            -- X red unless Y precedes
  evidence TEXT, created_at INTEGER NOT NULL,
  PRIMARY KEY (x, y)
);
```

- [ ] **Step 2: Failing tests** for `increments` CRUD and the ordering key. The candidate
  order is `(priority_class, stability, -size, age)` (spec §6.2):
  - `priority_class` rank: `test < invariant < fix < feature < rewrite` (tests first).
  - then higher `stability` first (less churn = earlier), then **larger** size last
    (`-size`), then older `age` first.

```python
def test_order_puts_tests_first():
    incs = [mk("feat", prio="feature"), mk("t", prio="test")]
    assert increments.order(incs)[0].priority_class == "test"

def test_order_key_is_total_and_deterministic(): ...
```

- [ ] **Step 3: Implement** `order(incs, dep_edges)` = topological sort honoring `dep_edge`
  (ground truth), tie-broken by the `(priority_class, -stability, size, age)` key. Hint `deps`
  is only a seed; `dep_edge` (inferred, §4.2) dominates.
- [ ] **Step 4:** Tests PASS. **Commit:** `feat(loom): increment store + ordering key`

---

## Task 3: Per-commit gate cache (§4.3) + per-commit runner

**Files:** `schema.py` (extend), `commitcache.py`, `tests/loom/test_commitcache.py`

Distinct from quilt's per-merge-point `gate_status`; this serves the **linear line** and is
keyed on `commit_tree_sha` so a fast-forward into `next_staging` is a pure cache-hit op (§4.3, P3).

- [ ] **Step 1:** Extend schema:

```sql
CREATE TABLE IF NOT EXISTS commit_gate (
  tree_sha TEXT NOT NULL, gate TEXT NOT NULL,
  status   TEXT NOT NULL,                       -- pass|fail
  result_ref TEXT, finished_at INTEGER NOT NULL,
  PRIMARY KEY (tree_sha, gate)
);
```

- [ ] **Step 2: Failing tests** — `run_commit_gate(repo, db, cfg, commit, gate)` returns cached
  result without re-running when `(tree_sha, gate)` is hot; re-runs when tree changes; routes
  every fresh run through `audit.verify_gate` (P1) so a fake-green never lands in the cache;
  identical trees at different commit shas share a cache row (the FF-cache-hot property).
- [ ] **Step 3: Implement** — lease a worktree from the pool at `commit`, run `gate.cmd`
  formatted with `{workdir}` (same convention as `quilt.gates`), build a `GateRun`
  (tree_sha, exit, stdout, expected_tests from cfg, coverage/diff paths), call
  `audit.verify_gate`; persist to `commit_gate` only on `real_green`.
- [ ] **Step 4:** `run_ladder_on_commit(...)` runs the configured ladder bottom-up over one
  commit, stopping at the first non-pass; returns the highest passed gate.
- [ ] **Step 5:** Tests PASS. **Commit:** `feat(loom): per-commit gate cache + audited runner`

---

## Task 4: Materialize + maximal green prefix (principles §2.1, §2.3)

**Files:** `linearize.py`, `tests/loom/test_linearize.py`

`materialize(order)` rebases each increment's patch onto the running tip in `order`,
producing a linear series; `staging` is force-updated to that series. The published green
branch is the **maximal green prefix** — everything past the first red seam is parked, never
materialized (green by truncation, never silent fixing).

- [ ] **Step 1: Failing tests** (real temp repos):
  - all-clean increments → full series materialized, `staging` tip == last increment.
  - second increment red on its gate → `staging` tip == first increment only; the red one
    and everything after is `parked` (a `work_queue` row), not on `staging`.
  - a **test-only** increment that is green on the current base is **hoisted to the front**
    and merged immediately (queue-skip, spec §6.2 *Tests-first*).

```python
def test_maximal_green_prefix_truncates(repo, db, cfg):
    res = linearize.solve(repo, db, cfg, increments=[a_green, b_red, c_green])
    assert res.staging_tip == res.commit_of(a_green)
    assert db.get_increment(b_red.id)["status"] == "parked"

def test_test_only_commit_hoisted(repo, db, cfg): ...
```

- [ ] **Step 2: Implement** `solve(...)`:
  1. `order = increments.order(set, dep_edges)`.
  2. hoist test-only-green-on-base increments to the front (queue-skip; merge immediately).
  3. for each in order: apply patch in a pool worktree, `commit_tree`, run the cheap ladder
     via `commitcache.run_ladder_on_commit`; on green, extend the prefix; on the first red,
     **stop** — this commit is a *seam* (Task 5).
  4. force-update `refs/loom/staging` to the green-prefix tip; park the rest.
- [ ] **Step 3:** Tests PASS. **Commit:** `feat(loom): materialize + maximal-green-prefix truncation`

---

## Task 5: Seam classification + reorder-before-repair (spec §6.2 decision hook)

**Files:** `linearize.py` (extend), `tests/loom/test_linearize.py`

At a red seam, the **one judgment a script can't make**: is this a *hard-dependency*
(→ reorder, non-destructive, free) or an *incidental-conflict* (→ repair, which is debt)?
Haiku: `(seam diff) -> {hard|incidental, easy_fix: bool}`. Everything else is deterministic.

- [ ] **Step 1: Failing tests** with an LLM stub:
  - hard-dependency verdict → linearizer records a `dep_edge(X→Y)` and **reorders** (retries
    the solve with Y before X) *before* any repair; reorder is non-destructive.
  - incidental-conflict verdict → no reorder; the seam is routed to repair (a `test_fail`/
    `conflict` work item for the P5 agent loop); the increment is `parked`.
  - reorder must be tried **before** repair (assert ordering of effects).

```python
def test_hard_dep_reorders_and_records_edge(repo, db, cfg, make_stub):
    cfg = cfg_with(seam_cmd=make_stub('{"kind":"hard","easy_fix":false}'))
    res = linearize.solve(repo, db, cfg, increments=[x_needs_y, y])
    assert ("x","y") in {(e["x"],e["y"]) for e in db.list_dep_edges()}
    assert res.order.index(y) < res.order.index(x_needs_y)
```

- [ ] **Step 2: Implement** the seam handler:
  - build the seam diff (the failing commit's patch + the failing gate output tail).
  - `verdict = loom.decide("linearizer", "seam", payload)` → re-runs the emitter (re-applies
    the candidate ordering) to verify, per §7.
  - `hard` → insert `dep_edge`, recompute `order`, retry `solve` (bounded retries to avoid
    thrash); `incidental` → enqueue repair work, park.
- [ ] **Step 3:** Tests PASS. **Commit:** `feat(loom): seam classifier — reorder before repair`

---

## Task 6: Cycle handling — interleave then split-needed (spec §6.2)

**Files:** `linearize.py` (extend), `tests/loom/test_linearize.py`

On a **series-level cycle** in the inferred DAG, drop to **commit-level interleave** (order
the individual commits of the cyclic increments rather than whole increments); if still
cyclic, emit **`split-needed`** (a work item asking an agent/human to split an increment).

- [ ] **Step 1: Failing tests** — construct `dep_edge` cycle A→B, B→A at increment level;
  assert the solver retries at commit granularity; construct an irreducible commit-level cycle
  and assert a `split-needed` work item is emitted (and the affected increments parked).
- [ ] **Step 2: Implement** cycle detection (DFS on `dep_edge`), commit-level expansion
  (each increment’s commits become orderable nodes), and the `split-needed` terminal.
- [ ] **Step 3:** Tests PASS. **Commit:** `feat(loom): cycle interleave + split-needed terminal`

---

## Task 7: Reflow epoch (§10) — thrash guard under force-push

**Files:** `epoch.py`, `linearize.py` (wire), `tests/loom/test_epoch.py`

Freeze the solved plan for an epoch; agents complete rebases against the frozen plan; results
are accepted **only if the epoch has not rolled**, else re-queued. Prevents thrash under
continuous force-push.

- [ ] **Step 1: Failing tests** — an epoch token is minted at `solve` time; submitting an
  agent result tagged with a stale epoch is **rejected and re-queued**; a current-epoch result
  is accepted; changing the increment set rolls the epoch.
- [ ] **Step 2: Implement** `Epoch` = monotone counter persisted in the DB, stamped on each
  `solve`; `accept(result, epoch)` compares against the current epoch.
- [ ] **Step 3:** Tests PASS. Run the **full quilt suite** → green. **Commit:**
  `feat(loom): reflow epoch guard`

---

## Definition of done (P2)

- [ ] `staging` is `materialize(sort(set, dep_edge, policy))`, recomputed/force-updated when
  the set changes; force-update is a normal operation, not an error path.
- [ ] Published `staging` is always the **maximal green prefix**; the remainder is parked,
  never materialized (green by truncation).
- [ ] **Zero** LLM calls on a clean all-green solve (asserted); Haiku is invoked **only** at a
  red seam, and only to classify hard vs incidental.
- [ ] Reorder is attempted before repair; inferred `dep_edge`s are recorded and durable.
- [ ] Per-commit gate cache is keyed on `commit_tree_sha`; every fresh green is auditor-verified
  (P1) before it lands in the cache.
- [ ] Worktree pool bounds concurrency and reaps on crash; reflow epoch rejects stale results.
- [ ] Full quilt suite green; only additive Loom modules/tables.
