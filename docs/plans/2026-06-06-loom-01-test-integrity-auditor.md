# Loom P1 — Test-Integrity Auditor (spec §6.1)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Steps use `- [ ]` checkboxes. Implement task-by-task; each task ends green.

**Goal:** Validate that a green signal is *real* before it is trusted anywhere. This is
the keystone (spec §2.5, §6.1): the whole system amplifies both the value of coverage and
the blast radius of its gaps, so this auditor is load-bearing, not optional. It runs after
**every** gate-ladder execution; a fake-green voids the cache entry and re-enqueues the gate.

**Architecture:** A deterministic Python checker (`quilt.loom.audit`) that inspects a gate
run's artifacts and the candidate tree. Decision hook (Haiku) fires *only* when the
deterministic checks are inconclusive (novel harness output). Reuses `quilt.gitio`,
`quilt.db`, `quilt.llm`.

**Depends on:** nothing (built first). **Consumers:** P2 (every per-commit gate), P3, P4.

**Tech stack:** Python 3.12 stdlib (sqlite3, subprocess, re), pytest. No new deps.

---

## File structure

```
quilt/
  src/quilt/loom/__init__.py        # new package; __version__
  src/quilt/loom/schema.py          # additive Loom tables (audit_result here; others later phases)
  src/quilt/loom/audit.py           # the auditor
  tests/loom/__init__.py
  tests/loom/conftest.py            # reuse quilt's Repo fixture; add gate-run artifact factory
  tests/loom/test_audit.py
```

All tests use real temp git repos (reuse `tests/conftest.py::repo`). No mocks; LLM is a
shell stub (the existing `make_stub` fixture pattern).

---

## Interfaces

```python
# quilt/loom/audit.py
@dataclass
class GateRun:
    mp_or_commit: str          # merge-point id (quilt) or commit sha (Loom per-commit)
    gate: str
    tree_sha: str              # tree the binary was supposedly built from
    exit_code: int
    stdout: str
    stderr: str
    expected_tests: int | None # from config/manifest if the harness declares it
    coverage_paths: list[str]  # files the run reported as covered (empty if none)
    diff_paths: list[str]      # files changed by the candidate vs its base

@dataclass
class Verdict:
    real_green: bool
    reason: str
    inconclusive: bool         # True → decision hook was consulted

def audit(run: GateRun, db, cfg, *, repo=None) -> Verdict: ...
```

Contract: `audit()` is called by the gate runner with the just-finished `GateRun`.
On `real_green=False`, the caller voids the cache row and re-enqueues the gate
(`db.record_gate(..., "fail")` + `db.enqueue_work("test_fail", ...)`).

---

## Task 1: Loom package skeleton + additive schema

**Files:** `src/quilt/loom/__init__.py`, `src/quilt/loom/schema.py`, `tests/loom/__init__.py`

- [ ] **Step 1:** Create `src/quilt/loom/__init__.py` with `__version__ = "0.1.0"`.
- [ ] **Step 2:** Write `schema.py` with an idempotent `apply(conn)` that `executescript`s
  only the Loom tables (so it composes with `quilt.db.DB`’s existing schema):

```python
AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_result (
  subject_id    TEXT NOT NULL,   -- merge-point id or commit sha
  gate          TEXT NOT NULL,
  tree_sha      TEXT NOT NULL,
  real_green    INTEGER NOT NULL,
  inconclusive  INTEGER NOT NULL,
  reason        TEXT NOT NULL,
  created_at    INTEGER NOT NULL,
  PRIMARY KEY (subject_id, gate, tree_sha)
);
"""
def apply(conn): conn.executescript(AUDIT_SCHEMA); conn.commit()
```

- [ ] **Step 3:** Add a `loom` console-script stub to `pyproject.toml`
  (`loom = "quilt.loom.cli:main"`) — `cli.main` may be a `raise SystemExit("not yet")`
  placeholder; real subcommands land per phase.
- [ ] **Step 4:** `tests/loom/__init__.py` empty; `tests/loom/conftest.py` re-exports the
  `repo`/`repo_with_branches` fixtures and adds a `gate_run` factory that builds a
  `GateRun` from kwargs.
- [ ] **Step 5:** Verify `from quilt.loom import audit` imports; `pytest -q` still green.
- [ ] **Commit:** `feat(loom): package skeleton + audit_result schema`

---

## Task 2: Deterministic checks (the load-bearing core)

**Files:** `src/quilt/loom/audit.py`, `tests/loom/test_audit.py`

The deterministic checks (spec §6.1 *Automated*), each independently fatal:

1. **expected-vs-actual test count** — if the harness declares N tests and the parsed
   summary shows `ran < N` (or 0), that is fake-green.
2. **exit code** — a green claim with nonzero exit is fake-green.
3. **rebuilt-from-candidate-tree** — the binary must have been built from `run.tree_sha`.
   Check the worktree the gate ran in was checked out at `tree_sha` (compare
   `gitio.tree_of(repo, "HEAD")` in the gate worktree, recorded at run time).
4. **coverage∩diff** — `set(coverage_paths) & set(diff_paths)` must be non-empty when the
   diff touches testable code; an empty intersection on a non-trivial diff is fake-green
   (the tests didn't exercise the change).
5. **no silent skip/xfail** — parse for `skipped`/`xfail`/`deselected` counts; a green that
   is green only because everything was skipped is fake-green.

- [ ] **Step 1: Failing tests** — one per check, plus a happy path:

```python
def test_real_green_passes(gate_run):
    run = gate_run(exit_code=0, stdout="5 passed", expected_tests=5,
                   coverage_paths=["a.c"], diff_paths=["a.c"], tree_sha="t1")
    v = audit.audit(run, db=None, cfg=cfg)
    assert v.real_green and not v.inconclusive

def test_zero_tests_is_fake_green(gate_run):
    run = gate_run(exit_code=0, stdout="0 passed", expected_tests=5,
                   coverage_paths=["a.c"], diff_paths=["a.c"], tree_sha="t1")
    assert not audit.audit(run, None, cfg).real_green

def test_nonzero_exit_is_fake_green(gate_run): ...
def test_all_skipped_is_fake_green(gate_run): ...
def test_coverage_misses_diff_is_fake_green(gate_run): ...
def test_count_mismatch_is_fake_green(gate_run): ...
```

- [ ] **Step 2: Implement** the deterministic checks as small pure predicates returning
  `(passed: bool, reason: str)`; `audit()` runs them in order, short-circuits on the first
  failure, returns `Verdict(real_green=False, reason=...)`. Parsing of counts is via a small
  set of regexes over common harness summaries (pytest, bazel, ctest); unknown summary →
  mark `inconclusive` and fall through to Task 3.
- [ ] **Step 3:** Persist the verdict via `schema.audit_result` when a `db` is passed.
- [ ] **Step 4:** Run tests → all PASS.
- [ ] **Commit:** `feat(loom): deterministic test-integrity checks`

---

## Task 3: Decision hook (Haiku) — only when inconclusive

**Files:** extend `audit.py`, `tests/loom/test_audit.py`

Spec §6.1 *Decision hook*: **only** when the deterministic checks are inconclusive (novel
harness output) → Haiku classifies real-green vs fake-green. This is the only place the
auditor calls a model.

- [ ] **Step 1: Failing tests** using an LLM stub (shell script echoing JSON):

```python
def test_inconclusive_consults_haiku(gate_run, make_stub):
    cfg = cfg_with(audit_cmd=make_stub('{"real_green": false, "reason": "harness aborted"}'))
    run = gate_run(exit_code=0, stdout="<novel harness blob>", expected_tests=None,
                   coverage_paths=[], diff_paths=["a.c"], tree_sha="t1")
    v = audit.audit(run, None, cfg)
    assert v.inconclusive and not v.real_green and "harness" in v.reason
```

- [ ] **Step 2: Implement** — when deterministic checks return inconclusive, build a prompt
  (run summary + the unparsed stdout tail) and call `quilt.llm.run_json(cfg.audit_cmd, prompt)`.
  Expected JSON contract: `{"real_green": bool, "reason": str}`. Set `inconclusive=True`.
  On `LLMError`, **fail closed**: `real_green=False, reason="auditor LLM error → treat as fake"`.
- [ ] **Step 3:** Config wiring — add `audit_cmd` to the `[llm]` section
  (default `claude -p --model claude-haiku-4-5`). Extend `gates.Config` parsing to carry it.
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Commit:** `feat(loom): Haiku decision hook for inconclusive audits`

---

## Task 4: Wire into the gate runner (the contract)

**Files:** `src/quilt/loom/audit.py` (a `verify_gate(...)` helper), `tests/loom/test_audit.py`

Spec §6.1 *Interfaces*: runs after every gate-ladder execution; a fake-green **voids the
cache entry and re-enqueues the gate**. Loom’s own gate runner (built in P2) calls this; we
ship the helper here so P2 just calls it.

- [ ] **Step 1: Failing test** — given a DB with a recorded `pass` gate row, a fake-green
  verdict must flip the row to `fail` and add a `test_fail` work item:

```python
def test_fake_green_voids_cache_and_requeues(repo, ...):
    db.record_gate("mp1", "unit", base, "pass")
    audit.verify_gate(repo, db, cfg, run=fake_green_run)   # auditor says fake
    assert db.gate_result("mp1", "unit", base) == "fail"
    assert db.pending_work()[0]["kind"] == "test_fail"
```

- [ ] **Step 2: Implement** `verify_gate(repo, db, cfg, run) -> Verdict`: call `audit()`; on
  `not real_green`, `db.record_gate(run.mp_or_commit, run.gate, base, "fail")` and
  `db.enqueue_work("test_fail", run.mp_or_commit, run.reason)`. Return the verdict so the
  caller can break the ladder.
- [ ] **Step 3:** Run tests → PASS. Run the **full quilt suite** (`pytest -q`) → still green
  (Loom is additive).
- [ ] **Commit:** `feat(loom): auditor gate-verification contract (void + requeue on fake-green)`

---

## Definition of done (P1)

- [ ] `audit()` correctly classifies the five deterministic failure modes + happy path.
- [ ] Haiku consulted **only** on inconclusive (asserted: zero LLM calls on the happy path).
- [ ] `verify_gate` voids the cache row and re-enqueues on fake-green; fails closed on LLM error.
- [ ] Verdicts persisted in `audit_result`, content-keyed on `(subject, gate, tree_sha)`.
- [ ] Full quilt suite green; Loom adds tables/modules only, edits no existing quilt module.
