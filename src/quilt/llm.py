"""Pluggable LLM commands (e.g. `claude -p`). The configured command receives
the prompt on stdin. run_json parses a JSON object from stdout (leniently:
first '{' to last '}', so chatty wrappers work). run_edit runs the command
inside a worktree where it is expected to edit files; exit 0 means done."""
import json
import shlex
import subprocess
import sys
from pathlib import Path


class LLMError(Exception):
    """LLM command failed or produced unusable output."""


def extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError(f"no JSON object in LLM output: {text[:200]!r}")
    try:
        return json.loads(text[start:end + 1])
    except ValueError as e:
        raise LLMError(f"bad JSON in LLM output: {e}") from e


def run_json(cmd: str, prompt: str, timeout: int = 600) -> dict:
    try:
        p = subprocess.run(shlex.split(cmd), input=prompt,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"llm command timed out after {timeout}s") from e
    if p.returncode != 0:
        raise LLMError(f"llm command failed ({p.returncode}): {p.stderr[-500:]}")
    return extract_json(p.stdout)


def run_edit(cmd: str, prompt: str, workdir: Path, timeout: int = 3600) -> bool:
    try:
        p = subprocess.run(shlex.split(cmd), input=prompt, cwd=workdir,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"llm edit command timed out after {timeout}s", file=sys.stderr)
        return False
    if p.returncode != 0:
        print(f"llm edit command failed ({p.returncode}): {p.stderr[-500:]}",
              file=sys.stderr)
        return False
    return True
