# Loom P4 — Single-Branch Path: Harvest + Coverage Gate + End-to-End (spec §6.3, §6.8)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Steps use `- [ ]` checkboxes.

**Goal:** Close the **single-branch path end-to-end** (spec §12 step 4):
harvest (§6.3) → linearize (P2) → stage → promote (P3), with the coverage gate (§6.8) wired
as a ladder rung. This is the first fully-deterministic, no-agent slice that produces a green,
bisectable `staging` and a stress-validated `next_staging` from one injected branch.

**Architecture:** `regression-lock harvest` lifts test-only commits to the base and merges
them immediately (no LLM); the coverage gate both enforces a coverage bar **and** certifies
that seam tests exercised the dependency they could trip (validity input for P2's inferred DAG).

**Depends on:** P2, P3. **Consumers:** P5 (agents plug into the same loop), P7.

**Tech stack:** Python 3.12 stdlib, pytest. Reuses `quilt.gitio`, `quilt.loom.linearize`,
`quilt.loom.commitcache`, `quilt.loom.audit`, `quilt.loom.promote`.

---

## File structure

```
src/quilt/loom/
  harvest.py       # NEW: regression-lock harvest (§6.3) — no LLM
  coverage.py      # NEW: coverage gate rung + seam-coverage certification (§6.8) — no LLM
  pipeline.py      # NEW: single-branch end-to-end driver (harvest→linearize→stage→promote)
tests/loom/
  test_harvest.py  test_coverage.py  test_pipeline.py
```

---

## Task 1: Regression-lock harvest (spec §6.3) — no LLM

**Files:** `harvest.py`, `tests/loom/test_harvest.py`

Spec §6.3: scan injected branches for commits whose tree **restricted to test-paths** applies
on `staging` and passes; lift them to base; merge immediately; rebase the donor branch. The
classifier and the lift-condition **coincide** (a test-only commit green on base = liftable).
Note: harvesting/landing an invariant changes the base tree → invalidates the combination
cache → **batch these at an epoch boundary, never dribble** (reuse P2's reflow epoch).

- [ ] **Step 1: Failing tests** (real repos):
  - a branch with a test-only commit (touches only test paths) that applies+passes on
    `staging` → lifted to base, merged immediately, donor rebased onto the new base.
  - a commit touching non-test paths → **not** harvested (left for the linearizer).
  - a test-only commit that fails on the current base → not lifted (queued instead).
  - harvested invariants are batched at an epoch boundary: two liftable commits land in one
    epoch transition, not as two separate base rewrites.

```python
def test_lifts_test_only_passing_commit(repo, db, cfg):
    lifted = harvest.run(repo, db, cfg, branch="feat")
    assert test_commit.id in {c.id for c in lifted}
    assert gitio.tree_of(repo, "refs/loom/staging") includes the test file

def test_skips_non_test_commit(repo, db, cfg): ...
def test_batches_at_epoch_boundary(repo, db, cfg): ...
```

- [ ] **Step 2: Implement** — "test-paths" is a configurable glob set (`[harvest] test_globs`).
  For each candidate commit, build its tree restricted to those globs (`git read-tree` /
  pathspec), test-apply on `staging` in a pool worktree, run the cheap ladder via
  `commitcache`; on green, merge immediately and rebase the donor. Gate base-changing lands
  through `epoch` so they batch.
- [ ] **Step 3:** Tests PASS. **Commit:** `feat(loom): regression-lock harvest`

---

## Task 2: Coverage gate (spec §6.8) — no LLM

**Files:** `coverage.py`, `tests/loom/test_coverage.py`

Two jobs (spec §6.8): (a) a pass/fail coverage bar as a ladder rung; (b) certify that the
seam tests at each commit **actually exercised the dependency they could trip** — the validity
input for P2's inferred DAG (an edge `X→Y` is only trustworthy if a test that would catch the
dependency actually ran the relevant code).

- [ ] **Step 1: Failing tests**:
  - coverage below the configured bar → gate `fail` (ladder breaks, work enqueued).
  - coverage at/above the bar → gate `pass`.
  - seam-coverage certification: given a `dep_edge(X→Y)` and a coverage report showing the
    edge's witnessing paths were executed → edge marked `validated`; if not executed → edge
    flagged `unwitnessed` (P2 must not trust it for reorder).
- [ ] **Step 2: Implement** — parse the coverage report the harness emits (reuse the
  `coverage_paths` already collected for the auditor in P1), compare to the bar, and
  cross-check witnessing paths against `dep_edge` evidence. Writes back an
  `evidence`/`witnessed` flag on the `dep_edge` row.
- [ ] **Step 3:** Register coverage as a gate in the ladder config so it slots into
  `commitcache.run_ladder_on_commit` like any other rung. Tests PASS.
- [ ] **Commit:** `feat(loom): coverage gate + seam-coverage certification`

---

## Task 3: Single-branch end-to-end driver

**Files:** `pipeline.py`, `tests/loom/test_pipeline.py`

Wire the slice: `harvest → linearize.solve → (staging) → promote.run → (next_staging)`, all
deterministic, **zero LLM calls** on the clean path.

- [ ] **Step 1: Failing test** — inject one branch with {a test-only commit, a feature commit};
  run `pipeline.run(repo, db, cfg, branch="feat")`; assert:
  - the test-only commit was harvested to base;
  - `staging` is the maximal green prefix (here, the full series);
  - a milestone cleared the candidate gate and `next_staging` fast-forwarded after stress;
  - **zero** `decide()`/LLM invocations (assert the stub was never called).
- [ ] **Step 2: Implement** the driver as a thin sequence over P1–P3 + Tasks 1–2.
- [ ] **Step 3:** Add `loom run --branch <b>` CLI. Tests PASS; full quilt suite green.
- [ ] **Commit:** `feat(loom): single-branch end-to-end pipeline`

---

## Definition of done (P4)

- [ ] Test-only commits are harvested to base and merged immediately; base-changing lands are
  batched at epoch boundaries (no dribble).
- [ ] Coverage is a ladder rung **and** certifies seam tests exercised their dependency (gating
  the trustworthiness of P2's inferred DAG).
- [ ] A single injected branch flows harvest→linearize→stage→promote producing a green,
  bisectable `staging` and a stress-validated `next_staging` with **zero LLM calls**.
- [ ] Full quilt suite green; additive only.
