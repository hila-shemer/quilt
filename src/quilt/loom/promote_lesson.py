"""Cross-role lesson promotion + doctrine leak gate (Loom spec §9.4).

**Promotion** (deterministic): a role-local lesson is promoted to
`project-global` when the same `pattern` was learned **independently by ≥2 roles**
(cross-role recurrence). The promoted lesson sums recurrence and **evicts** the
role-local instances it supersedes — roles share only through global, no middle
tier.

**Leak gate** (HARD SAFETY GATE, §9.4 / §6.7 rules): `project-global` →
`doctrine-upstream` is a sanitize-and-generalize step, never a copy. Opus
(`decide("promoter", "leak", …)`) produces the generic form; a deterministic
re-check rejects any residual project-internal token (fail closed). The job only
**stages** the candidate (a doctrine patch artifact + a `doctrine-upstream`
journal entry) for approval — it contains **no push**. Pushing, if approved, is
the operator's action per the propose-push gate.
"""
from dataclasses import dataclass
from pathlib import Path

from . import decide, journal

# Project-internal tokens that must never survive into doctrine-upstream. The
# deterministic re-check fails closed on any of these (extensible).
DENYLIST = ("majestic", "hila-shemer")


class LeakBlocked(Exception):
    """The sanitized candidate is unusable (no generic form, or a project-internal
    token survived) — nothing is staged."""


@dataclass
class LeakCandidate:
    lesson_id: int
    generic_text: str
    artifact: Path
    journal_id: int


def promote_cross_role(db) -> list[int]:
    """Promote role-local patterns learned by ≥2 distinct roles to project-global.
    Returns the new global lesson ids."""
    rows = [journal._row_to_lesson(r) for r in db.conn.execute(
        "SELECT * FROM role_journal WHERE scope='role-local' AND pattern IS NOT NULL "
        "ORDER BY id")]
    by_pattern: dict[str, list] = {}
    for l in rows:
        by_pattern.setdefault(l.pattern, []).append(l)

    promoted = []
    for pattern, ls in by_pattern.items():
        if len({l.role for l in ls}) < 2:            # not cross-role → keep local
            continue
        total = sum(l.recurrence for l in ls)
        task_types = {l.task_type for l in ls}
        tt = ls[0].task_type if len(task_types) == 1 else "*"
        rep = sorted(ls, key=lambda x: (-x.recurrence, x.id))[0]   # representative form
        gid = journal.append(db, "global", tt, rep.lesson, kind=rep.kind,
                             pattern=pattern, refs=rep.refs, scope="project-global",
                             recurrence=total)
        for l in ls:
            journal.delete(db, l.id)                 # evict what global supersedes
        promoted.append(gid)
    return promoted


def _residual_internal(text: str) -> list[str]:
    low = text.lower()
    return [t for t in DENYLIST if t in low]


def stage_doctrine(db, cfg, lesson_id: int, outdir: Path) -> LeakCandidate:
    """Sanitize+generalize a project-global lesson and STAGE it as a
    doctrine-upstream candidate. Never pushes. Raises LeakBlocked if no generic
    form is produced or a project-internal token survives the re-check."""
    l = journal.get(db, lesson_id)
    if l is None or l.scope != "project-global":
        raise ValueError("leak gate only operates on project-global lessons")

    prompt = (
        "Generalize this project lesson into GENERIC engineering doctrine for an "
        "upstream library. Strip ALL project-internal names, products, and people; "
        "keep only the transferable principle.\n"
        f"lesson: {l.lesson}\n"
        'Reply JSON: {"generic": "<one or two sentences>"}')
    v = decide.decide_json(cfg, "promoter", prompt, db=db, task_type="leak")
    generic = (v or {}).get("generic", "").strip()
    if not generic:
        raise LeakBlocked("promoter produced no generic form")
    residual = _residual_internal(generic)
    if residual:                                     # deterministic re-check, fail closed
        raise LeakBlocked(f"residual project-internal tokens: {residual}")

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    artifact = outdir / f"doctrine-{lesson_id}.md"
    artifact.write_text(generic + "\n")
    jid = journal.append(db, "doctrine", l.task_type, generic, kind=l.kind,
                         pattern=l.pattern, scope="doctrine-upstream",
                         recurrence=l.recurrence)
    return LeakCandidate(lesson_id=lesson_id, generic_text=generic,
                         artifact=artifact, journal_id=jid)
