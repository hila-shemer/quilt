"""Decision-context retrieval (Loom spec §9.2, §7).

Resolves global pollution: **project-global** lessons are small and always loaded;
**role-local** lessons are large and retrieved **by relevance** to the current
signal — never wholesale — within a token budget (~2k tokens/call, §7).

Relevance is a dependency-free token-overlap score between a lesson's retrieval
key (`pattern` + `refs`) and the current signal (the decision prompt + the files
in play). **Sub-decision flagged to the maintainer:** an embedding model would
rank better but adds a dependency; the default ships without one. Swap the
`_relevance` scorer if approved.
"""
import re

from . import journal

_TOK = re.compile(r"[A-Za-z0-9_]+")


def _tokens(text: str | None) -> set[str]:
    return {t.lower() for t in _TOK.findall(text or "")}


def _signal_tokens(signal: str, files) -> set[str]:
    toks = _tokens(signal)
    for f in files:
        toks |= _tokens(f)
    return toks


def _lesson_text(l) -> str:
    parts = [p for p in (l.pattern, l.lesson) if p]
    return " — ".join(parts)


def _relevance(l, sig_tokens: set[str]) -> int:
    key = _tokens((l.pattern or "") + " " + " ".join(l.refs))
    return len(key & sig_tokens)


def context_for(db, role: str, task_type: str, files=(), signal: str = "", *,
                budget_words: int = 500, max_local: int | None = None) -> str:
    """Compose role context: all project-global lessons (always) + the most
    relevant role-local lessons within the budget. Returns a markdown string."""
    if db is None:
        return ""
    sig = _signal_tokens(signal, files)
    lines: list[str] = []

    globals_ = journal.by_scope(db, "project-global")
    if globals_:
        lines.append("# Project knowledge (always loaded)")
        lines += [f"- {_lesson_text(l)}" for l in globals_]

    locals_ = [l for l in journal.by_role(db, role, task_type) if l.scope == "role-local"]
    ranked = sorted(((_relevance(l, sig), l) for l in locals_),
                    key=lambda sl: (-sl[0], -sl[1].recurrence, sl[1].id))
    relevant = [l for score, l in ranked if score > 0]   # exclude zero-overlap

    picked, used = [], 0
    for l in relevant:
        if max_local is not None and len(picked) >= max_local:
            break
        w = len(_lesson_text(l).split())
        if used + w > budget_words:
            break
        picked.append(l)
        used += w

    if picked:
        lines.append(f"# Relevant {role} experience")
        for l in picked:
            tag = f" (x{l.recurrence})" if l.recurrence > 1 else ""
            lines.append(f"- {_lesson_text(l)}{tag}")
    return "\n".join(lines)
