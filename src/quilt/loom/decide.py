"""The universal decision-call wrapper (Loom spec §5, §7).

Every LLM decision hook goes through here: it injects role context (global +
role-local journal; a stub until P6 wires retrieval), shells out via quilt.llm,
and returns the parsed result. The *deterministic re-check* (re-running the
emitter to verify the result) is the caller's responsibility — no model output
is trusted without it.
"""
from .. import llm


def context_for(db, role: str, task_type: str, files=()) -> str:
    """Role context injected into a decision prompt. P6 replaces this with real
    retrieval (global always-in + role-local by relevance). Stub: empty."""
    return ""


def decide_json(cfg, role: str, prompt: str, *, db=None, task_type: str = "") -> dict | None:
    """Run a classifier/attributor decision. Returns the parsed JSON dict, or
    None when no command is configured or the call fails (caller decides the
    safe default). Command is cfg.llm['<role>_cmd']."""
    cmd = (getattr(cfg, "llm", None) or {}).get(f"{role}_cmd")
    if not cmd:
        return None
    ctx = context_for(db, role, task_type)
    full = f"{ctx}\n{prompt}" if ctx else prompt
    try:
        return llm.run_json(cmd, full)
    except llm.LLMError:
        return None
