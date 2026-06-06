# Loom P7 — Maintainer Loop + Zoo Composition + Port-Forward (spec §6.4, §6.5)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Steps use `- [ ]` checkboxes.

**Goal:** Keep the **zoo** of long-lived design-alternative branches green against a moving
`next` (§6.4), compose them on demand without enumerating beyond quilt's N≤5 window, and
**port-forward** behaviors across a language/project boundary using a differential oracle
(§6.5).

**Architecture:** The maintainer loop is **no-LLM** (it delegates conflicts to the resolver
from P5/§6.6 via the standard signal). Port-forward is an **Opus** producer whose output is a
**branch** that re-enters the front of the pipeline via P2/P4.

**Depends on:** P2–P6 (the loops, the agents, the memory). **Consumers:** P8.

**Tech stack:** Python 3.12 stdlib, pytest. Reuses `quilt.gitio`, `quilt.probe` (the N≤5
combination window + `branches` list), `quilt.loom.linearize`, `quilt.loom.commitcache`,
`quilt.llm` (for port-forward).

---

## File structure

```
src/quilt/loom/
  zoo.py        # NEW: maintainer loop (§6.4) — no LLM; feeds quilt's `branches` list
  compose.py    # NEW: on-demand composition (rebase Y onto next_X), N≤5 window
  portforward.py# NEW: §6.5 differential-oracle harness + Opus porting decision hook
tests/loom/
  test_zoo.py  test_compose.py  test_portforward.py
```

---

## Task 1: Maintainer loop (spec §6.4) — no LLM

**Files:** `zoo.py`, `tests/loom/test_zoo.py`

Spec §6.4: for each zoo singleton, rebase onto the moving `next`; run its DoD gates; on
success update its tip in **quilt's `branches` list**; on conflict/red emit the standard
signal into the loop. Keeps the lattice basis current so on-demand composition is cheap.
Scaling: maintain N singletons (linear); compositions are materialized on demand, never
enumerated beyond quilt's N≤5 window.

- [ ] **Step 1: Failing tests** (real repos):
  - a zoo singleton that rebases cleanly onto an advanced `next` and passes its DoD gates →
    its tip is updated and written into quilt's `branches` config (the maintainer→quilt feed,
    spec §11); transient red during rebase is allowed (zoo invariant, spec §3).
  - a singleton that conflicts on rebase → emits the standard `conflict` work signal (routed to
    the P5 resolver), does **not** itself call an LLM.
  - a singleton whose DoD gates end red → emits `test_fail`; tip not advanced.
- [ ] **Step 2: Implement** `maintain(repo, db, cfg)` iterating `refs/loom/zoo/*`: rebase onto
  `next` in a pool worktree, run DoD gates via `commitcache` (auditor-verified), update tip +
  write `branches` on success, else `db.enqueue_work(...)`. Per-branch DoD = which test sets
  must end green (from the increment's `dod`).
- [ ] **Step 3:** Tests PASS. **Commit:** `feat(loom): zoo maintainer loop (no LLM)`

---

## Task 2: On-demand composition (spec §6.4 scaling)

**Files:** `compose.py`, `tests/loom/test_compose.py`

Compositions are materialized **on demand** (rebase Y onto `next_X`), never enumerated beyond
quilt's N≤5 window. This is where Loom hands a viable combination to the linearizer.

- [ ] **Step 1: Failing tests** — composing two green singletons X,Y yields a rebased series
  (Y onto `next_X`) handed to `linearize.solve`; requesting a composition beyond N=5 is
  refused (defer to quilt's `enumerate_combos` ValueError, spec §6.4); a composition reuses
  quilt's merge-probe to confirm viability **before** the expensive rebase.
- [ ] **Step 2: Implement** `compose(repo, db, cfg, members)`: call `quilt.probe` for viability
  (cheap), then rebase-materialize via P2 only for viable combos.
- [ ] **Step 3:** Tests PASS. **Commit:** `feat(loom): on-demand zoo composition`

---

## Task 3: Port-forward (spec §6.5) — Opus producer

**Files:** `portforward.py`, `tests/loom/test_portforward.py`

Spec §6.5 *Automated*: detect what changed in `next`/source since fork (new/changed behaviors
and their oracle cases); run the differential oracle against the target; identify behaviors not
yet matched. *Decision hook*: **Opus** performs the actual porting development for unmatched
behaviors; **Done = ported behavior matches the oracle**. (Source diffs do not rebase across a
language boundary; oracle cases do.) Output is a **branch** which re-enters the front of the
pipeline via P2/P4.

- [ ] **Step 1: Failing tests** (stub oracle + stub Opus edit):
  - `unmatched_behaviors(source, target, oracle)` returns the oracle cases failing on the
    target (deterministic).
  - the Opus hook (`loom.decide("porter", "port", ...)` → `run_edit`) is invoked per unmatched
    behavior; success is gated by **re-running the oracle** (not by the model's say-so, §7).
  - the produced branch is registered as an increment and enters P2 (assert it appears in the
    pipeline's input set), not merged directly.
- [ ] **Step 2: Implement** the differential-oracle harness (run oracle cases against target,
  diff results) + the Opus porting loop with deterministic oracle re-check after each edit.
- [ ] **Step 3:** Tests PASS; full quilt suite green. **Commit:**
  `feat(loom): port-forward via differential oracle (Opus)`

---

## Definition of done (P7)

- [ ] Zoo singletons are kept green against a moving `next` with **no LLM** in the maintainer
  loop; conflicts/reds emit standard signals to the P5 agents.
- [ ] Maintainer updates quilt's `branches` list (the §11 feed); compositions are on-demand,
  viability-probed by quilt first, and never enumerated beyond N≤5.
- [ ] Port-forward matches behaviors to a differential oracle; Opus output is gated by oracle
  re-run and re-enters the pipeline as a branch, never merged blind.
- [ ] Full quilt suite green; additive only.
