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
| **P3** Promotion ff+stress + push gate (§6.6/§6.7) | `loom-03-...` | ⬜ next |
| P4 single-branch path (§6.3/§6.8) | `loom-04-...` | ⬜ |
| P5 agent loops (§8) — **rightwayc** | `rightwayc/.../loom-05-agents.md` | ⬜ |
| P6 memory pipeline (§9) | `loom-06-...` | ⬜ |
| P7 maintainer/zoo/port-forward (§6.4/§6.5) | `loom-07-...` | ⬜ |
| P8 cross-repo coordination (§6.10) | `loom-08-...` | ⬜ |

**Suite:** 137 passed; the only reds are 2 pre-existing failures from the missing
`git-mediate` binary (unrelated to Loom). Loom test count: 47.

## Modules built (`src/quilt/loom/`)

`schema.py` (additive tables: audit_result, increment, dep_edge, commit_gate, loom_meta) ·
`audit.py` (auditor + `verify_gate`) · `worktree.py` (pool) · `increments.py` (store +
`order()`) · `commitcache.py` (per-commit tree-keyed cache + audited runner) ·
`linearize.py` (`solve` / `solve_seams`, maximal-green-prefix, reorder-before-repair,
cycle→split-needed) · `decide.py` (universal LLM wrapper; `context_for` is a stub until P6) ·
`epoch.py` (reflow guard) · `cli.py` (stub).

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

## Next: P3

Consume `refs/loom/staging` + the per-commit cache (FF preserves commit identity → cache hot;
only the `long` gate is new work). Build `milestone.py`, `promote.py`, `pushgate.py` per
`loom-03-...`. Confirm milestone spacing (spec §13) before shipping.
