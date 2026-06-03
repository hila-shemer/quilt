"""Drain the work queue through the cheap triage model. Routing only — it
never fixes anything (design §5). trivial/moderate → 'triaged' (eligible for
the capable agent); complex → 'deferred'."""
from . import llm

EFFORT_CLASSES = ("trivial", "moderate", "complex")

PROMPT = """\
You are a triage classifier for a git integration pipeline.
Given a work item, estimate the cause and classify the effort to fix as
trivial, moderate or complex. Respond with ONLY a JSON object:
{{"est_cause": "<one sentence>", "effort_class": "trivial|moderate|complex"}}

Work item kind: {kind}
Merge point: {target_id}
Detail:
{detail}
"""


def drain(db, cfg) -> dict:
    cmd = cfg.llm.get("triage_cmd")
    if not cmd:
        raise llm.LLMError("no [llm] triage_cmd configured")
    report = {"triaged": 0, "deferred": 0, "errors": 0}
    for item in db.pending_work():
        prompt = PROMPT.format(kind=item["kind"], target_id=item["target_id"],
                               detail=item["detail"] or "")
        try:
            verdict = llm.run_json(cmd, prompt)
            effort = verdict["effort_class"]
            if effort not in EFFORT_CLASSES:
                raise llm.LLMError(f"bad effort_class: {effort!r}")
        except (llm.LLMError, KeyError):
            report["errors"] += 1
            continue
        db.record_triage(str(item["id"]), item["target_id"], item["kind"],
                         verdict.get("est_cause", ""), effort, model=cmd)
        if effort == "complex":
            db.set_work_state(item["id"], "deferred")
            report["deferred"] += 1
        else:
            db.set_work_state(item["id"], "triaged")
            report["triaged"] += 1
    return report
