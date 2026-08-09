"""Presentation layer: what an operator sees when a gate fails.

The rule these tests encode: a failure report shows the failure. Never the
head of the log, never a mid-word cut, never a wall of `ok:` lines.
"""
from quilt import report

EXHIBIT_B = (
    "c7_dev.s\n"
    + "".join(f"ok: c7_{i}.s\n" for i in range(200))
    + "FAIL: c7_kbd_ovf.s expected 0x1f got 0x00\n"
    "FAIL: c7_irq.s timeout\n"
    "emu: unhandled trap at 0x8000\n"
)


def test_salient_lines_shows_failures_not_the_passing_prefix():
    out = report.salient_lines(EXHIBIT_B, limit=20)
    assert any("FAIL: c7_kbd_ovf.s" in line for line in out)
    assert any("FAIL: c7_irq.s" in line for line in out)
    assert not any(line.startswith("ok: ") for line in out)


def test_salient_lines_never_emits_a_partial_line():
    """Exhibit A: truncation cut 'Merge' into 'erge'. Lines are whole or absent."""
    text = "Merge errored on the replay pair:\ntrace-q: error: META missing key\n"
    out = report.salient_lines(text, limit=20)
    assert "Merge errored on the replay pair:" in out
    assert "trace-q: error: META missing key" in out


def test_salient_lines_keeps_the_tail_when_nothing_matches():
    text = "".join(f"line {i}\n" for i in range(100))
    out = report.salient_lines(text, limit=5)
    assert [line for line in out if not line.startswith("…")] == [
        f"line {i}" for i in range(95, 100)
    ]


def test_salient_lines_marks_omitted_lines():
    out = report.salient_lines(EXHIBIT_B, limit=20)
    assert any(line.startswith("…") and "omitted" in line for line in out)


def test_salient_lines_respects_the_budget():
    out = report.salient_lines(EXHIBIT_B, limit=3)
    content = [line for line in out if not line.startswith("…")]
    assert len(content) == 3


def test_salient_lines_of_empty_detail():
    assert report.salient_lines("", limit=20) == []


def test_salient_lines_drops_blank_lines():
    out = report.salient_lines("first\n\n\n\nlast\n", limit=20)
    assert out == ["first", "last"]


def test_member_label_lists_branch_names():
    mp = {"member_branches": ["toolchain", "emu-c"], "member_patch_ids": ["a1", "b2"]}
    assert report.member_label(mp) == "toolchain + emu-c"


def test_member_label_falls_back_to_patch_ids_for_pre_migration_rows():
    mp = {"member_branches": None, "member_patch_ids": ["a1b2c3d4e5f6a7b8", "ffff"]}
    assert report.member_label(mp) == "a1b2c3d4e5f6 + ffff"
