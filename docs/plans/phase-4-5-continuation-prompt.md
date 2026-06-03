# Continuation prompt — Quilt Phases 4–5

Paste this into a fresh session, working in `/home/shemer/quilt`:

---

Work in `/home/shemer/quilt`, branch `feature/agent-skills` (Phases 0–3 are tagged `milestone/phase-3`).

Read first: `mds/quilt-design.md` (design — §5 triage, §6 skills table, §7 phases, §8 open problems) and `README.md`. The plan for the existing core: `docs/plans/2026-06-03-quilt-pipeline.md`. Run tests: `.venv/bin/pytest -q` (43 green).

Implement Phases 4–5: agent skills + promotion loop.

State: deterministic core works (probe → gates → tick → promote/poison CLI). The work queue (`work_queue` table: kind `conflict` | `test_fail`) is the only LLM entry point. `triage` + `frankenmerge_fix` tables exist; nothing writes them. `validated`/`inflight` validation states never set. Promotion writes `refs/quilt/target/<name>`.

Phase 4 — wire LLM agents to the queue:
1. `quilt triage` — drain pending work via small/fast model: classify `{est_cause, effort_class}` into `triage`; route trivial/moderate → resolve queue, complex → defer.
2. Conflict-resolution skill (capable model): worktree merge → resolve semantic conflicts → pin `refs/quilt/<id>`, `construction=agent`/`frankenmerge` (+`frankenmerge_fix` row when adding glue commits on top).
3. Test-failure diagnosis skill: attribute fail to a member or resolution; emit poison verdict (use `quilt poison <prefix>` and cascade eviction).
4. LLM access is the user's concern: shell out to `claude -p` non-interactive; pluggable command in `quilt.toml` so tests stub deterministically.

Phase 5 — promotion automation + back-prop: validated state on long-gate pass; frozen `main-candidate` tag → `t4day` → `main` advance; `frankenmerge_fix` back-prop drive (pending → offered → adopted).

Conventions: TDD; tests use real temp git repos (see `tests/conftest.py`); no mocks; subagent-driven dev per task; stubbed LLM in tests (fixture script echoing canned JSON). Reuse existing CLI as a pattern (exit 1 on routing error). Don't break existing 43 tests. Remind the user to open a JIRA ticket before starting.
