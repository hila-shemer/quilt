import pytest
from quilt import llm
from tests.conftest import make_stub


def test_run_json_parses_object_amid_noise(tmp_path):
    stub = make_stub(tmp_path, "triage.sh",
        '#!/bin/sh\ncat >/dev/null\necho preamble\n'
        'echo \'{"est_cause": "rename", "effort_class": "trivial"}\'\n')
    out = llm.run_json(str(stub), "prompt text")
    assert out == {"est_cause": "rename", "effort_class": "trivial"}


def test_run_json_raises_on_nonzero_exit(tmp_path):
    stub = make_stub(tmp_path, "fail.sh", "#!/bin/sh\nexit 3\n")
    with pytest.raises(llm.LLMError):
        llm.run_json(str(stub), "x")


def test_run_json_raises_on_garbage(tmp_path):
    stub = make_stub(tmp_path, "garbage.sh",
                     "#!/bin/sh\ncat >/dev/null\necho not json at all\n")
    with pytest.raises(llm.LLMError):
        llm.run_json(str(stub), "x")


def test_run_edit_runs_in_workdir(tmp_path):
    stub = make_stub(tmp_path, "edit.sh",
                     "#!/bin/sh\ncat >/dev/null\ntouch edited.marker\n")
    work = tmp_path / "work"
    work.mkdir()
    assert llm.run_edit(str(stub), "fix it", work) is True
    assert (work / "edited.marker").exists()


def test_run_edit_false_on_failure(tmp_path):
    stub = make_stub(tmp_path, "fail.sh", "#!/bin/sh\nexit 1\n")
    work = tmp_path / "work"
    work.mkdir()
    assert llm.run_edit(str(stub), "fix it", work) is False


def test_run_json_timeout_raises_llmerror(tmp_path):
    stub = make_stub(tmp_path, "slow.sh", "#!/bin/sh\nsleep 5\n")
    with pytest.raises(llm.LLMError):
        llm.run_json(str(stub), "x", timeout=1)


def test_run_edit_failure_reports_stderr(tmp_path, capsys):
    stub = make_stub(tmp_path, "fail.sh",
                     "#!/bin/sh\necho boom >&2\nexit 1\n")
    work = tmp_path / "work"
    work.mkdir()
    assert llm.run_edit(str(stub), "fix it", work) is False
    assert "boom" in capsys.readouterr().err
