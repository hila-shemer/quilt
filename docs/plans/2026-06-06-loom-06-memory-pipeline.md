# Loom P6 — Memory Pipeline (spec §9)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Steps use `- [ ]` checkboxes.

**Goal:** Give the agent roles durable, retrievable experience without polluting context.
Three scopes, three cadences (spec §9.1): **role-local** (`(role, task_type)`-keyed, large,
retrieved by relevance), **project-global** (small, always loaded, stays local), and
**doctrine-upstream** (generic, project-internal-free — the only scope that may reach
rightwayc, behind the §9.4 leak gate). The continuous per-task reflection-append already
exists in rightwayc (the `review-brief` mechanism); Loom adds **compaction**, **promotion**,
and **retrieval**.

**Architecture:** `role_journal` table (§4.4) as the store; deterministic retrieval +
similarity index in Python; compaction (nightly, Haiku) and promotion (weekly/threshold,
Opus) as scheduled jobs through `loom.decide`. The leak gate **stages** doctrine-upstream
candidates and never pushes (it follows P3's propose-push rules).

**Depends on:** P5 (the roles that generate journal entries) and P1–P4. **Consumers:** every
agent call's context injection (spec §7).

**Tech stack:** Python 3.12 stdlib + the relevance index. Retrieval similarity uses a
dependency-free approach (token-overlap / hashing) unless the maintainer approves adding an
embedding model; flagged as a sub-decision in Task 2.

---

## File structure

```
src/quilt/loom/
  schema.py        # extend: role_journal table (§4.4)
  journal.py       # NEW: append/query role_journal; scope tagging
  retrieve.py      # NEW: §9.2 retrieval (global always-in; role-local by relevance)
  compact.py       # NEW: §9.3 nightly compaction (Haiku)
  promote_lesson.py# NEW: §9.4 weekly/threshold promotion (Opus) + leak gate (stages only)
tests/loom/
  test_journal.py  test_retrieve.py  test_compact.py  test_promote_lesson.py
```

---

## Task 1: role_journal store (spec §4.4) + scope tagging

**Files:** `schema.py` (extend), `journal.py`, `tests/loom/test_journal.py`

```sql
CREATE TABLE IF NOT EXISTS role_journal (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  role       TEXT NOT NULL,            -- resolver|reviewer|debugger|fixer|...
  task_type  TEXT NOT NULL,            -- conflict|fix-review|...
  kind       TEXT NOT NULL,            -- structured|narrative
  pattern    TEXT,                     -- retrieval key (structured: conflict/finding shape)
  lesson     TEXT NOT NULL,            -- text or action
  recurrence INTEGER NOT NULL DEFAULT 1,
  refs       TEXT NOT NULL DEFAULT '[]', -- json [files/symbols] for decay detection
  scope      TEXT NOT NULL,            -- role-local|project-global|doctrine-upstream
  created_at INTEGER NOT NULL
);
```

- [ ] **Step 1: Failing tests** — append a structured lesson and a narrative reflection;
  query by `(role, task_type)`; recurrence defaults to 1 ("hypothesis at 1, rule at N").
- [ ] **Step 2: Implement** `append(...)`, `by_role(role, task_type)`, `bump_recurrence(id)`.
- [ ] **Step 3:** Tests PASS. **Commit:** `feat(loom): role_journal store + scope tagging`

---

## Task 2: Retrieval (spec §9.2) — resolves global pollution

**Files:** `retrieve.py`, `tests/loom/test_retrieve.py`

Spec §9.2: **global** is small and always in context; **role-local** is large, indexed, and
retrieved **by relevance** to the current signal — never loaded wholesale. Structured
experience (pattern→action) retrieved by similarity; narrative reflection used for
context-priming. ~2k tokens of role context per call is the budget (spec §7).

- [ ] **Step 1: Failing tests**:
  - `context_for(role, task_type, files, signal)` always includes **all** `project-global`
    lessons (small, gated) and **never** the whole role-local set.
  - the top-K role-local structured lessons are those most similar to the signal/files;
    irrelevant lessons are excluded; total stays within a token budget.
- [ ] **Step 2: Implement** the relevance ranker (token-overlap over `pattern`+`refs` vs. the
  signal; dependency-free default). **Sub-decision (flag to maintainer):** swap in an embedding
  model only if approved; default ships without new deps.
- [ ] **Step 3:** Wire `retrieve.context_for` into `loom.decide`'s context-injection seam
  (the stub from P2 master §5 now returns real role-local + global context).
- [ ] **Step 4:** Tests PASS. **Commit:** `feat(loom): relevance retrieval for decision context`

---

## Task 3: Compaction (spec §9.3) — nightly, per-role, Haiku

**Files:** `compact.py`, `tests/loom/test_compact.py`

Spec §9.3: dedup restatements, merge duplicates (increment recurrence), supersede contradicted
lessons, evict lessons referencing **dead code** (decay detection via `refs`). Recurrence is
the signal it produces.

- [ ] **Step 1: Failing tests**:
  - duplicate lessons (same pattern) merge into one with `recurrence` summed.
  - a lesson whose `refs` point at files/symbols no longer present in the repo is evicted
    (deterministic decay check via `gitio`).
  - a contradicted lesson is superseded (the Haiku step decides contradiction; stubbed).
- [ ] **Step 2: Implement** — deterministic dedup + decay first (no LLM); Haiku
  (`loom.decide("compactor", "merge", ...)`) **only** for the contradiction/supersede judgment.
- [ ] **Step 3:** Schedule as a nightly job through the scheduler (one job per role).
  Tests PASS. **Commit:** `feat(loom): nightly journal compaction`

---

## Task 4: Promotion (spec §9.4) + leak gate — Opus, stages only

**Files:** `promote_lesson.py`, `tests/loom/test_promote_lesson.py`

Spec §9.4 *Promotion*: promote a lesson when it is durable, non-redundant, and **cross-role
recurrent** (two roles independently learning the same thing); evict what it supersedes; roles
share **only** through global (no middle tier). Spec §9.4 *Leak gate* (**hard safety gate**):
project-global → doctrine-upstream is a **sanitization-and-generalization** step, never a copy;
project-specific lessons stay local; only the *generic* form may be **staged** for upstream;
the job **stages** candidates and **never pushes** (promotion follows P3's propose-push rules).

- [ ] **Step 1: Failing tests**:
  - a lesson learned by two distinct roles (cross-role recurrent) → promoted to
    `project-global`; what it supersedes is evicted.
  - a single-role lesson, however recurrent, is **not** promoted to global.
  - the leak gate produces a **sanitized, generalized** candidate (project-internal names
    stripped) tagged `doctrine-upstream`, and writes it to a staging area — **no push** occurs
    (assert intent only, mirroring P3's pushgate tests).
- [ ] **Step 2: Implement** — cross-role recurrence is deterministic (count distinct roles for
  a pattern); the generalize/sanitize step is Opus via `loom.decide("promoter", "leak", ...)`,
  re-checked deterministically for residual project-internal tokens (a denylist of
  Majestic-specific identifiers) before staging. Staging target is a rightwayc-bound patch
  artifact, surfaced for approval per P3 — never an auto-push into `rightwayc/doctrine.md`.
- [ ] **Step 3:** Tests PASS; full quilt suite green. **Commit:**
  `feat(loom): cross-role lesson promotion + doctrine leak gate (stage only)`

---

## Definition of done (P6)

- [ ] Global context is always loaded and small; role-local is retrieved by relevance within a
  token budget; nothing is loaded wholesale.
- [ ] Compaction dedups/decays deterministically and uses Haiku **only** for contradiction.
- [ ] Promotion requires cross-role recurrence; the leak gate sanitizes+generalizes and
  **stages** doctrine candidates for approval — it never pushes into rightwayc.
- [ ] `loom.decide` now injects real role-local + global context (closing the P2 stub).
- [ ] Full quilt suite green; additive only.
