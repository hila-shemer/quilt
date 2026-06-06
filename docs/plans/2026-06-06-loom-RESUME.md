# Loom — Resume / Handoff

**Branch (all repos):** `claude/rightwayc-spec-implementation-XU5Sk`
**Spec:** `docs/plans/2026-06-06-loom-spec.md` · **Master plan:** `docs/plans/2026-06-06-loom-00-master.md`

Loom = rebase-based orchestration layer on quilt (+ rightwayc agents). Engine is an
**additive** `src/quilt/loom/` package (imports/reuses quilt; edits no existing module).

## Status

| Phase | Plan doc | State |
|-------|----------|-------|
| Plans | all `loom-*` docs (quilt) + `loom-agents-design` / `loom-05-agents` (rightwayc) | ✅ written |
| **P1** Test-integrity auditor (§6.1) | `loom-01-...` | ✅ **done** — `audit.py`, 15 tests |
| **P2** Linearizer + seam-search (§6.2/§4.3/§10) | `loom-02-...` | ✅ **done** — worktree/increments/commitcache/linearize/decide/epoch, 32 tests |
| **P3** Promotion ff+stress + push gate (§6.6/§6.7) | `loom-03-...` | ✅ **done** — milestone/promote/pushgate/cli, 18 tests |
| **P4** single-branch path (§6.3/§6.8) | `loom-04-...` | ✅ **done** — harvest/coverage/pipeline + `loom run`, 16 tests |
| P5 agent loops (§8) — **rightwayc** | `rightwayc/.../loom-05-agents.md` | ⬜ next |
| **P6** memory pipeline (§9) | `loom-06-...` | ✅ **done** — journal/retrieve/compact/promote_lesson, 25 tests |
| P7 maintainer/zoo/port-forward (§6.4/§6.5) | `loom-07-...` | ⬜ next |
| P8 cross-repo coordination (§6.10) | `loom-08-...` | ⬜ |

**Suite:** 198 passed (this host has `git-mediate` installed, so the 2 pre-existing
`git-mediate` reds pass here; in a container lacking it they remain the only reds,
unrelated to Loom). Loom test count: 106.

## Modules built (`src/quilt/loom/`)

`schema.py` (additive tables: audit_result, increment, dep_edge, commit_gate, loom_meta) ·
`audit.py` (auditor + `verify_gate`) · `worktree.py` (pool) · `increments.py` (store +
`order()`) · `commitcache.py` (per-commit tree-keyed cache + audited runner) ·
`linearize.py` (`solve` / `solve_seams`, maximal-green-prefix, reorder-before-repair,
cycle→split-needed) · `decide.py` (universal LLM wrapper; `context_for` is a stub until P6) ·
`epoch.py` (reflow guard) · `milestone.py` (per-increment tips + mutable suffix/frozen
floor) · `promote.py` (FF-from-staging + milestone stress; FF-only refuses rewrite) ·
`pushgate.py` (propose-push hard safety gate — never pushes, hila-shemer/not-Majestic
URL rule mirroring rightwayc `green_verify.py`, patch artifacts on block) ·
`harvest.py` (regression-lock harvest — lifts test-only commits to base, one
`epoch.roll` per pass, rebases donor) · `coverage.py` (coverage-bar gate +
`certify_edges` writing `dep_edge.witnessed`) · `pipeline.py` (single-branch
harvest→linearize→promote driver) · `cli.py` (`loom run`, `loom promote`,
`loom propose-push`) · `journal.py` (role_journal store + scope tagging) ·
`retrieve.py` (§9.2 relevance retrieval — global always-in + role-local top-K;
now backs `decide.context_for`, closing the P2 stub) · `compact.py` (nightly
dedup/decay deterministic + Haiku-only contradiction supersede) ·
`promote_lesson.py` (cross-role→global promotion + doctrine leak gate — sanitize,
deterministic denylist re-check, stage artifact, never push).

## Env setup (do this first in a fresh container)

```bash
cd /home/user/quilt
python3.12 -m venv .venv && .venv/bin/pip install -e . pytest      # pyproject needs >=3.12
git config --global commit.gpgsign false                            # signing server returns 400 → commits/tests fail otherwise
.venv/bin/pytest -q                                                 # baseline: 137 pass, 2 git-mediate fails
.venv/bin/pytest tests/loom -q                                      # loom only
```

## Conventions to keep

- **Additive only:** new `loom/` modules + `schema.apply()` tables; never edit existing quilt
  modules. Reuse `gitio`, `db`, `gates.Config`, `llm.run_json`/`run_edit`, `resolve`,
  `candidate`, `backprop`.
- **TDD, commit per task:** failing test → implement → green → commit `feat(loom): ...` ending
  with the session URL. Run the full suite before the final commit of a phase.
- **Decision contract:** all LLM hooks go through `loom.decide`; deterministic re-check after.
  Zero LLM calls on the happy path (assert it). LLM stubs in tests = shell scripts via
  `tests.conftest.make_stub`; config under `cfg.llm["<role>_cmd"]` (free-form dict, no Config change).
- **Safety:** no component pushes a remote (P3 propose-push gate reuses rightwayc
  `tools/mergeq/green_verify.py` for the `hila-shemer`/not-`Majestic` URL rule).

## Next: P7 — maintainer loop / zoo / port-forward (§6.4/§6.5), in quilt

Build `loom-07-...`: the maintainer loop rebases each zoo singleton onto the moving `next`,
runs its DoD gates, updates its tip in quilt's `branches` on success, and routes
conflict/red into the loop (delegating conflicts to promotion §6.6). Compositions stay
on-demand (rebase Y onto `next_X`), never enumerated beyond quilt's N≤5 window. Port-forward
(§6.5) is the cross-language/cross-project rebase (LLM: Opus) — gate it behind the
propose-push rules like every external surface.

**Also still open:** **P5 — agent loops (§8) lives in the `rightwayc` repo**
(`.../loom-05-agents.md`), not quilt: the builder/debugger/seam agents that drive this
engine. The quilt engine is complete through the single-branch path **and** the memory
pipeline (P6) — agents enqueue/consume `work_queue` items (`test_fail`, `conflict`,
`split_needed`, `coverage_fail`), call into `pipeline`/`linearize`/`promote`, and write/read
the `role_journal` via `journal`/`retrieve` (already wired into every `decide` call). P8
(cross-repo coordination §6.10) remains after that.

### P6 notes (for the next worker)
- **`decide.context_for` is now real** (delegates to `retrieve.context_for`): every LLM hook
  gets all `project-global` lessons + relevance-ranked top-K `role-local` (token-overlap over
  `pattern`+`refs` vs the prompt/files; dependency-free — embedding model is a flagged
  sub-decision). Budget is `budget_words` (default 500 ≈ 2k tokens); globals always in.
- **Memory cadences are callable, scheduling is external:** `compact.run_all(db, repo, cfg)`
  (nightly, per role) and `promote_lesson.promote_cross_role(db)` (weekly/threshold). Wire to
  cron/`loom` CLI when convenient — no quilt scheduler edit was made.
- **Leak gate fails closed:** `promote_lesson.stage_doctrine` raises `LeakBlocked` if no
  generic form or any `DENYLIST` token (`majestic`, `hila-shemer`) survives; it only writes a
  patch artifact + a `doctrine-upstream` journal row — **no push** (extend `DENYLIST` as new
  project-internal names appear).

### P4 notes (for the next worker)
- **Harvest integrates by advancing the `cfg.base` ref itself** (the local `next` trunk);
  `linearize.solve` reads `cfg.base`, so no P2 edit was needed. `harvest.run` returns lifted
  base shas, calls `epoch.roll(db)` **once** per pass (base-tree change → cache invalidation,
  batched), and rebases the donor via worktree cherry-pick (lifted commits drop out by
  patch-id). `test_globs` is a `run()` arg (default `harvest.DEFAULT_TEST_GLOBS`); the CLI
  reads `[harvest] test_globs` straight from the toml (load_config doesn't carry it — no
  Config change).
- **Coverage is two jobs:** `coverage.gate` (bar rung; enqueues `coverage_fail`, does NOT go
  through the test-summary auditor — it's a measurement gate) and `coverage.certify_edges`
  (writes `dep_edge.witnessed`; witnessing paths come from `dep_edge.evidence` — a path-list
  or path-like tokens; free-text evidence → unwitnessed, P2 must not trust it for reorder).
- **`pipeline.run`** = harvest → make one increment per remaining branch commit → `solve` →
  `promote`. Clean path is zero-LLM (asserted via a marker stub on `audit_cmd`/`seam_cmd`).

### P3 notes
- `promote.run` advances **one milestone at a time** (lowest milestone strictly above the
  floor), via `commitcache` so only the new `long`/stress gate is marginal work; FF-only,
  `NonFastForward` guards the frozen floor. Stress config lives at `cfg.promotion["stress"]`
  (a full gate dict: `{name, cmd, test, expected?}`); default `{name:"long", cmd:"true",
  test:False}`.
- Milestone spacing (spec §13): default = tip of each absorbed increment. In the
  single-commit-per-increment model `solve()` produces, that is one milestone per staging
  commit; `milestone.milestones()` already chunks by per-increment commit count so a future
  multi-commit increment collapses to a single tip milestone.
- `pushgate.propose` is the **only** push surface and contains no `git push`. Block path:
  raise `PushBlocked` without `outdir`, or emit `.patch` artifacts + return a blocked proposal
  with `outdir`. `refs/loom/staging` raises `NotPushable` first, unconditionally.
