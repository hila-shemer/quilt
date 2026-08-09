"""Presentation layer: turning stored failure detail into something an operator
can act on without opening .quilt.sqlite3.

Two rules drive everything here. Lines are whole or absent — a report that cuts
'Merge' down to 'erge' has spent its budget on nothing. And the budget belongs
to the failure, not to the run-up: a gate log is mostly evidence that the parts
which worked, worked, and none of that is why you are reading it.
"""
import re

# Lines worth the budget. Deliberately prefix-matching on 'error' so 'errored'
# and 'errors' count, and loose enough to catch a linker or an emulator that
# never heard of a test framework.
_FAIL_RE = re.compile(
    r"\bFAIL\w*|\bERROR|\berror|\bTraceback\b|\bassert\w*|\bpanic\b"
    r"|\bfatal\b|\bnot ok\b|Segmentation fault|undefined reference"
    r"|\bexception\b|\baborted\b|\btimed out\b|\btimeout\b",
    re.IGNORECASE)

# Lines that are evidence of success. Never worth the budget when a failure
# line exists, and a wall of them is the signature of a report gone wrong.
_NOISE_RE = re.compile(
    r"\s*(ok|pass|passed|skip|skipped|\[\s*ok\s*\]|\.+)\b", re.IGNORECASE)

_OMITTED = "… ({n} line{s} omitted)"


def _marker(n: int) -> str:
    return _OMITTED.format(n=n, s="" if n == 1 else "s")


def salient_lines(text: str, limit: int = 20) -> list[str]:
    """Up to *limit* whole lines of *text*, chosen failure-first.

    Failure-matching lines win the budget; whatever is left goes to the tail,
    walking back from the end and stopping at the first success line (the tail
    of a failing log is where the failure usually is, but the run-up is not).
    With no failure line anywhere, this degrades to a plain tail. Gaps are
    reported, so the reader knows the report is an excerpt.
    """
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    live = [i for i, ln in enumerate(lines) if ln.strip()]
    if not live or limit <= 0:
        return []

    hits = [i for i in live
            if _FAIL_RE.search(lines[i]) and not _NOISE_RE.match(lines[i])]
    if hits:
        chosen = set(hits[-limit:])
        for i in reversed(live):
            if len(chosen) >= limit:
                break
            if i in chosen:
                continue
            if _NOISE_RE.match(lines[i]):
                break
            chosen.add(i)
    else:
        chosen = set(live[-limit:])

    out, gap = [], 0
    for i in live:
        if i not in chosen:
            gap += 1
            continue
        if gap:
            out.append(_marker(gap))
            gap = 0
        out.append(lines[i])
    if gap:
        out.append(_marker(gap))
    return out


def work_header(item: dict, mp: dict | None) -> str:
    """`#3  test_fail  c5ffdee8c6d3  [toolchain + emu-c]  gate=tests exit=1`"""
    parts = [f"#{item['id']}", f"{item['kind']:9}", item["target_id"][:12]]
    if mp:
        parts.append(f"[{member_label(mp)}]")
    if item.get("gate"):
        parts.append(f"gate={item['gate']}")
    if item.get("exit_code") is not None:
        parts.append(f"exit={item['exit_code']}")
    if item.get("state") and item["state"] != "queued":
        parts.append(f"state={item['state']}")
    if item.get("dismiss_reason"):
        parts.append(f"reason={item['dismiss_reason']}")
    return "  ".join(parts)


def work_block(item: dict, mp: dict | None, limit: int = 20,
               full: bool = False) -> list[str]:
    """Header plus indented detail — an excerpt by default, all of it on *full*."""
    detail = item.get("detail") or ""
    body = (detail.splitlines() if full
            else salient_lines(detail, limit=limit))
    return [work_header(item, mp)] + [f"    {line}" for line in body]


def status_row(mp: dict, *, state: str, highest: str | None,
               failed: str | None) -> str:
    row = (f"{mp['id'][:12]} {mp['construction']:9} {state:9} "
           f"gate={highest or '-'}")
    if failed:
        row += f" fail={failed}"
    return f"{row}  [{member_label(mp)}]"


def display_state(mp: dict, failed_gate: str | None) -> str:
    """What the operator needs to know, in precedence order.

    validation_state is a scheduling field; a merge-point whose gate failed is
    failed even while that field still reads 'untested'.
    """
    if mp["validation_state"] == "poison":
        return "poison"
    if failed_gate:
        return "failed"
    return mp["validation_state"]


def member_label(mp: dict) -> str:
    """'toolchain + emu-c' — which branches this merge-point is about.

    Pre-migration rows have no branch names stored; short patch-ids are a worse
    answer than names but a better one than nothing.
    """
    names = mp.get("member_branches")
    if names:
        return " + ".join(names)
    return " + ".join(p[:12] for p in mp.get("member_patch_ids") or [])
