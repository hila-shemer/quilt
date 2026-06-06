"""Nightly journal compaction (Loom spec §9.3) — per-role.

Three operations, deterministic-first:
  - **dedup** restatements: merge lessons sharing a `(task_type, kind, pattern,
    scope)` key into the earliest, summing `recurrence` (the rule-at-N signal).
  - **decay**: evict lessons whose `refs` all point at code no longer in the repo
    (dead-code detection via git) — purely deterministic.
  - **supersede**: the ONLY LLM step — Haiku (`decide("compactor", "merge", …)`)
    judges whether two same-role lessons contradict; the superseded one is
    dropped. Skipped entirely when no `compactor_cmd` is configured.

Intended to run nightly, one job per role (scheduled externally; `run`/`run_all`
are the entry points).
"""
from itertools import combinations
from pathlib import Path

from .. import gitio
from . import decide, journal


def dedup(db, role: str, task_type: str | None = None) -> int:
    """Merge same-pattern lessons into the earliest; return how many were merged
    away. Recurrence is summed (duplicates increment the rule's strength)."""
    groups: dict[tuple, list] = {}
    for l in journal.by_role(db, role, task_type):
        if not l.pattern:
            continue
        groups.setdefault((l.task_type, l.kind, l.pattern, l.scope), []).append(l)
    merged = 0
    for ls in groups.values():
        if len(ls) < 2:
            continue
        ls.sort(key=lambda x: x.id)
        keep, total = ls[0], sum(x.recurrence for x in ls)
        db.conn.execute("UPDATE role_journal SET recurrence=? WHERE id=?",
                        (total, keep.id))
        for x in ls[1:]:
            db.conn.execute("DELETE FROM role_journal WHERE id=?", (x.id,))
            merged += 1
    db.conn.commit()
    return merged


def _ref_alive(repo: Path, ref: str) -> bool:
    """A ref is alive if it is a tracked path or its token appears anywhere in the
    tracked tree."""
    if gitio.git(repo, "ls-files", "--", ref):
        return True
    return gitio._run(repo, "grep", "-l", ref, check=False).returncode == 0


def evict_dead(db, repo: Path, role: str | None = None) -> list[int]:
    """Evict lessons all of whose `refs` reference code no longer in the repo.
    Lessons with no refs are never evicted. Returns the evicted ids."""
    lessons = (journal.by_role(db, role) if role
               else [journal._row_to_lesson(r)
                     for r in db.conn.execute("SELECT * FROM role_journal ORDER BY id")])
    evicted = []
    for l in lessons:
        if l.refs and not any(_ref_alive(repo, r) for r in l.refs):
            journal.delete(db, l.id)
            evicted.append(l.id)
    return evicted


def supersede(db, cfg, role: str, task_type: str | None = None) -> list[int]:
    """Drop lessons contradicted by a later one (Haiku decides). No-op without a
    `compactor_cmd`. Returns the superseded ids."""
    lessons = sorted(journal.by_role(db, role, task_type), key=lambda l: l.id)
    removed: set[int] = set()
    for a, b in combinations(lessons, 2):
        if a.id in removed or b.id in removed:
            continue
        prompt = (
            "Two lessons learned by the same role. Do they CONTRADICT — does one "
            "supersede the other?\n"
            f"a (id={a.id}): {a.lesson}\nb (id={b.id}): {b.lesson}\n"
            'Reply JSON: {"contradicts": bool, "keep": "a"|"b"|"both"}')
        v = decide.decide_json(cfg, "compactor", prompt, db=db, task_type="merge")
        if not v or not v.get("contradicts"):
            continue
        keep = v.get("keep")
        loser = a if keep == "b" else (b if keep == "a" else None)
        if loser is not None:
            journal.delete(db, loser.id)
            removed.add(loser.id)
    return sorted(removed)


def run(db, repo: Path, cfg, role: str, task_type: str | None = None) -> dict:
    """Compact one role: dedup → decay → supersede. Returns a summary."""
    merged = dedup(db, role, task_type)
    evicted = evict_dead(db, repo, role)
    superseded = supersede(db, cfg, role, task_type)
    return {"role": role, "merged": merged,
            "evicted": len(evicted), "superseded": len(superseded)}


def run_all(db, repo: Path, cfg) -> list[dict]:
    """Compact every role present in the journal (one nightly job per role)."""
    roles = [r["role"] for r in
             db.conn.execute("SELECT DISTINCT role FROM role_journal ORDER BY role")]
    return [run(db, repo, cfg, role) for role in roles]
