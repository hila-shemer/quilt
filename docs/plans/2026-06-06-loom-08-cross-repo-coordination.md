# Loom P8 — Cross-Repo Coordination (spec §6.10)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Steps use `- [ ]` checkboxes. This is the **last** phase (spec §12.8).

**Goal:** Land atomic dual-repo small changes, and detect cross-pipeline dependency cycles and
**refuse rather than deadlock** (spec §6.10). Deterministic, no LLM.

**Architecture:** A coordinator that treats a cross-repo change as a single increment spanning
two repos (`patches: {repo → patch}`, already modeled in §4.1), materializes both sides
together, and gates on both pipelines being green at the joined point. Cross-pipeline cycle
policy defaults to **refuse-and-park** (decision §13 — confirm before shipping).

**Depends on:** P3 (promotion/push gate), P7 (zoo/compose). **Consumers:** none (terminal phase).

**Tech stack:** Python 3.12 stdlib, pytest. Reuses `quilt.gitio` across multiple repo paths,
`quilt.loom.linearize`, `quilt.loom.promote`, `quilt.loom.pushgate`.

---

## File structure

```
src/quilt/loom/
  crossrepo.py   # NEW: atomic dual-repo landing + cross-pipeline cycle detection
tests/loom/
  test_crossrepo.py
```

The `increment.patches` JSON already carries `{repo → patch}` (multi-repo), and for staging the
spec restricts these to "small diff + small cross-repo diff only" (§4.1). P8 makes that
multi-repo increment land atomically.

---

## Task 1: Atomic dual-repo small-change landing

**Files:** `crossrepo.py`, `tests/loom/test_crossrepo.py`

- [ ] **Step 1: Failing tests** (two real temp repos, e.g. an `e2`-like and an `mss`-like pair):
  - a cross-repo increment with patches in both repos materializes both sides, runs each repo's
    gate ladder at the joined point, and advances **both** `next_staging` refs only if **both**
    sides are green (all-or-nothing).
  - if one side is red, **neither** ref advances; the increment is parked with a `test_fail`
    work item naming the failing repo.
  - the push side reuses P3's propose-push gate per repo (no auto-push; `hila-shemer` URL check
    on each).
- [ ] **Step 2: Implement** `land_cross(repos: dict[str, Path], db, cfg, increment)`:
  materialize each repo's patch via P2, gate each, and gate the **join** (both green) before
  any advance; advance is two `promote.run` calls guarded by a single all-green check.
- [ ] **Step 3:** Tests PASS. **Commit:** `feat(loom): atomic dual-repo landing`

---

## Task 2: Cross-pipeline cycle detection — refuse, don't deadlock

**Files:** `crossrepo.py` (extend), `tests/loom/test_crossrepo.py`

Spec §6.10: detect cross-pipeline dependency cycles and **refuse rather than deadlock**.
Default policy: refuse-and-park (alternative: human-arbitrated break — decision §13).

- [ ] **Step 1: Failing tests** — construct a cycle where repo-A increment requires repo-B
  increment to precede and vice versa; assert the coordinator **refuses** (raises/parks with a
  clear cross-pipeline-cycle reason) and **never blocks** waiting; assert an acyclic
  cross-repo dependency chain lands in dependency order.
- [ ] **Step 2: Implement** a cross-pipeline dependency graph over both repos' `dep_edge`
  tables + the cross-repo increment links; run cycle detection (DFS); on a cycle, park all
  members with a `cross_repo_cycle` work item and return a refusal (configurable policy hook
  for the future human-arbitrated-break alternative).
- [ ] **Step 3:** Tests PASS; full quilt suite green. **Commit:**
  `feat(loom): cross-pipeline cycle refusal (no deadlock)`

---

## Definition of done (P8)

- [ ] Cross-repo small changes land atomically (both sides green or neither advances).
- [ ] Each side's external push goes through P3's propose-push gate (no auto-push, `hila-shemer`
  verified).
- [ ] Cross-pipeline cycles are **refused and parked**, never deadlocked; acyclic chains land
  in order.
- [ ] Full quilt suite green; additive only.

---

## Programme completion

With P8 green, the full Loom build order (spec §12) is implemented: auditor → linearizer →
promotion → single-branch path → agents → memory → zoo/port-forward → cross-repo. The
whole-programme DoD in `loom-00-master.md` §9 now holds.
