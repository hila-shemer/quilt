# Loom P3 — Promotion (ff + milestone-stress) + Propose-Push Gate (spec §6.6, §6.7)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Steps use `- [ ]` checkboxes.

**Goal:** Advance `next_staging` by **fast-forward from staging** at validated milestones,
paying only the marginal cost of the stress (`long`) gate (§6.6); and gate every external
push behind a **hard safety boundary** that never auto-pushes (§6.7).

**Architecture:** Extend quilt's `freeze`/`advance` (`quilt.candidate`) to the linear line.
The propose-push gate reuses rightwayc's `green_verify.py` rule (URL must contain
`hila-shemer`, never `Majestic`) and emits patch artifacts on any block.

**Depends on:** P2 (the maximal-green `staging` + per-commit cache). **Consumers:** P4, P7, P8.

**Tech stack:** Python 3.12 stdlib, pytest. Reuses `quilt.candidate.freeze/advance`,
`quilt.gitio`, `quilt.loom.commitcache`, `quilt.loom.audit`. Shells to rightwayc's
`tools/mergeq/green_verify.py` (configurable path) for the URL/lockfile certification.

---

## File structure

```
src/quilt/loom/
  milestone.py     # NEW: milestone selection (tip of absorbed increment) + frozen-floor mgmt
  promote.py       # NEW: ff-from-staging + milestone stress (extends quilt.candidate)
  pushgate.py      # NEW: propose-push hard safety gate (§6.7)
tests/loom/
  test_milestone.py  test_promote.py  test_pushgate.py
```

---

## Task 1: Milestone selection + frozen floor (spec §3, §6.6)

**Files:** `milestone.py`, `tests/loom/test_milestone.py`

A milestone defaults to **the tip of a fully-absorbed increment** (decision §13 — confirm
spacing before shipping). `next_staging`'s tip is a **frozen floor**: staging may rewrite only
the suffix above it. The seam-search working set is exactly that mutable suffix and shrinks
from below as milestones validate.

- [ ] **Step 1: Failing tests** — given a solved `staging` series with increments A,B,C fully
  absorbed, `milestones(staging)` returns the per-increment tip commits in order; given a
  `next_staging` tip at A, `mutable_suffix(staging, next_staging)` returns commits B,C only.
- [ ] **Step 2: Implement** `milestones(repo, db, staging_ref)` (walk the series, mark
  increment boundaries from the increment store) and `mutable_suffix(...)` (commits in
  `next_staging..staging`).
- [ ] **Step 3:** Tests PASS. **Commit:** `feat(loom): milestone selection + frozen floor`

---

## Task 2: FF promotion + milestone stress (spec §6.6)

**Files:** `promote.py`, `tests/loom/test_promote.py`

Spec §6.6: pick the staging milestone that cleared the candidate gate; run the stress (`long`)
gate via quilt's async `untested → inflight → validated`; on `validated`, **fast-forward**
`next_staging`; on stress-fail, **hold** `next_staging`, let staging run ahead, route the
failure to the debugger (P5/§6.10) for attribution + fix on the mutable suffix, re-stress.

- [ ] **Step 1: Failing tests** (real repos, stub `long` gate):
  - milestone clears candidate gate + stress passes → `refs/loom/next_staging` fast-forwards
    to the milestone commit; FF preserves commit identity so the per-commit cache stays hot
    (assert no gate re-run for commits already cached).
  - stress fails → `next_staging` **unchanged**; a `test_fail` work item is enqueued
    (attribution routed to the debugger), and `staging` is free to advance past the milestone.
  - FF only: refuse a non-fast-forward promotion (the floor never rewrites).

```python
def test_validated_milestone_fast_forwards(repo, db, cfg):
    promote.run(repo, db, cfg)                       # candidate gate already green on M
    assert gitio.read_ref(repo, "refs/loom/next_staging") == milestone_M_sha

def test_stress_fail_holds_floor(repo, db, cfg): ...
def test_ff_only_refuses_rewrite(repo, db, cfg): ...
```

- [ ] **Step 2: Implement** by composing quilt's `candidate.freeze`/`advance` semantics onto
  the linear line: `freeze` the milestone commit (write `refs/loom/candidate/next_staging`),
  run the `long` gate through `commitcache` (auditor-verified), and on `validated`
  fast-forward `refs/loom/next_staging` (refusing if not an ancestor-advance). On fail, hold
  and enqueue. Reuse quilt's `untested → inflight → validated` validation-state machine.
- [ ] **Step 3:** Tests PASS. **Commit:** `feat(loom): ff promotion + milestone stress`

---

## Task 3: Propose-push gate — HARD SAFETY GATE (spec §6.7, principle §2.6)

**Files:** `pushgate.py`, `tests/loom/test_pushgate.py`

Spec §6.7: **never pushes.** Surfaces the materialized result + proposed branch name for
interactive approval; verifies the push URL contains `hila-shemer` and **not** `Majestic`;
on any block, emits patch-file artifacts instead. `refs/loom/staging` (local) is never pushed.

- [ ] **Step 1: Failing tests** (no network; assert *intent*, never an actual push):
  - `propose(remote_url, ref)` returns a `PushProposal` (target URL, branch name, commit,
    diffstat) and performs **no** push; the proposal is the only output.
  - a URL containing `Majestic` (or not containing `hila-shemer`) is **rejected** before any
    proposal is surfaced.
  - on rejection/block, `propose` writes patch artifacts (`git format-patch` of
    `next..next_staging`) to the configured out-dir and returns their paths.
  - `refs/loom/staging` is hard-coded as non-pushable: attempting to propose it raises.

```python
def test_rejects_majestic_url(repo, cfg):
    with pytest.raises(pushgate.PushBlocked):
        pushgate.propose(repo, cfg, url="git@github.com:Majestic/e2.git", ref="refs/loom/next_staging")

def test_emits_patches_on_block(repo, cfg, tmp_path):
    arts = pushgate.propose(repo, cfg, url="git@github.com:Majestic/e2.git",
                            ref="refs/loom/next_staging", outdir=tmp_path).artifacts
    assert arts and all(p.suffix == ".patch" for p in arts)

def test_never_pushes_staging(repo, cfg):
    with pytest.raises(pushgate.NotPushable):
        pushgate.propose(repo, cfg, url=valid_url, ref="refs/loom/staging")
```

- [ ] **Step 2: Implement** — the URL check delegates to rightwayc's
  `tools/mergeq/green_verify.py` semantics (configurable `green_verify_cmd`); reuse it rather
  than re-implementing the `hila-shemer`/`Majestic` rule. `propose()` returns a proposal
  object for the human approval surface (CLI prints it; an integration may wrap it). The
  module contains **no `git push`** call at all — pushing, if approved, is the operator's
  action outside Loom (or a separately-audited, explicitly-approved step). Document this.
- [ ] **Step 3:** Tests PASS. **Commit:** `feat(loom): propose-push hard safety gate`

---

## Task 4: CLI wiring + full-suite check

**Files:** `src/quilt/loom/cli.py`, `tests/loom/test_cli_promote.py`

- [ ] **Step 1:** Add `loom promote` (run Task 2) and `loom propose-push <remote> <ref>`
  (run Task 3, print the proposal or the artifact paths). Global flags mirror quilt
  (`--repo`, `--config`, `--db`).
- [ ] **Step 2: Tests** — `loom promote` advances `next_staging` on a validated fixture;
  `loom propose-push` prints a proposal and pushes nothing.
- [ ] **Step 3:** Full quilt suite green. **Commit:** `feat(loom): promote + propose-push CLI`

---

## Definition of done (P3)

- [ ] `next_staging` advances **only** by fast-forward from a stress-validated milestone;
  commit identity is preserved so the per-commit cache stays hot (only the `long` gate is new work).
- [ ] Stress failure holds the floor, lets staging run ahead, and routes attribution out.
- [ ] **No component pushes a remote.** `propose-push` surfaces a proposal, verifies
  `hila-shemer`/not-`Majestic` via `green_verify.py`, emits patches on block, and refuses
  `refs/loom/staging` outright.
- [ ] Full quilt suite green; additive only.
