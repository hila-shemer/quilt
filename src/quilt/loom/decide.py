"""The universal decision-call wrapper (Loom spec §5, §7).

Every LLM decision hook goes through here: it injects role context (global
always-in + role-local journal by relevance, P6 §9.2), shells out via quilt.llm,
and returns the parsed result. The *deterministic re-check* (re-running the
emitter to verify the result) is the caller's responsibility — no model output
is trusted without it.
"""
from .. import llm
from . import retrieve


def context_for(db, role: str, task_type: str, files=(), signal: str = "") -> str:
    """Role context injected into a decision prompt: global always-in + role-local
    by relevance (P6 §9.2). Empty when no db is supplied."""
    return retrieve.context_for(db, role, task_type, files=files, signal=signal)


def decide_json(cfg, role: str, prompt: str, *, db=None, task_type: str = "",
                files=()) -> dict | None:
    """Run a classifier/attributor decision. Returns the parsed JSON dict, or
    None when no command is configured or the call fails (caller decides the
    safe default). Command is cfg.llm['<role>_cmd']. The decision prompt doubles
    as the retrieval signal for role-local context."""
    cmd = (getattr(cfg, "llm", None) or {}).get(f"{role}_cmd")
    if not cmd:
        return None
    ctx = context_for(db, role, task_type, files=files, signal=prompt)
    full = f"{ctx}\n{prompt}" if ctx else prompt
    try:
        return llm.run_json(cmd, full)
    except llm.LLMError:
        return None
