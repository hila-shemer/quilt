"""Configurable monotone gate ladder; results keyed by base commit (staleness =
absence of a row for the current base)."""
import os
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import gitio

# How much of a failing gate's output to keep. The tail is what matters, so an
# over-long log loses its head — at a line boundary, and it says so.
MAX_DETAIL_BYTES = 512 * 1024


class ConfigError(Exception):
    """quilt.toml cannot be read as the run needs it: a key is absent, or it is
    present and empty where a value has to mean something.

    The empty case is the reason this is a type of its own rather than a
    KeyError. `subprocess.run("", shell=True)` exits 0, so a `final_cmd` that is
    present but empty is a command that "passes" without running — a check that
    cannot fail — and a `final_gate` with no name writes a gate_status row under
    the empty string. Reading those as legitimate values is what
    `candidate.advance` refuses by name."""


@dataclass
class Config:
    base: str
    branches: list[str]
    gates: list[dict]          # [{name, cmd, long?}]
    targets: dict[str, str]    # target -> required gate
    llm: dict[str, str] = field(default_factory=dict)        # triage_cmd | resolve_cmd | diagnose_cmd
    promotion: dict = field(default_factory=dict)            # target, candidate_gate, final_gate, final_cmd

    @property
    def ladder(self):
        return [g["name"] for g in self.gates]


def load_config(path: Path) -> Config:
    raw = tomllib.loads(Path(path).read_text())
    return Config(base=raw["quilt"]["base"], branches=raw["quilt"]["branches"],
                  gates=raw.get("gate", []), targets=raw.get("targets", {}),
                  llm=raw.get("llm", {}), promotion=raw.get("promotion", {}))


def _clip(text: str) -> str:
    """Trim to MAX_DETAIL_BYTES from the end, never mid-line."""
    if len(text) <= MAX_DETAIL_BYTES:
        return text
    tail = text[-MAX_DETAIL_BYTES:]
    nl = tail.find("\n")
    if nl != -1:
        tail = tail[nl + 1:]
    return (f"… ({len(text) - len(tail)} bytes truncated; showing the tail)\n"
            + tail)


def run_gate_cmd(cmd: str, workdir: str) -> subprocess.CompletedProcess:
    """Run one gate command with stdout and stderr sharing a single pipe.

    Two streams captured apart and concatenated do not reconstruct into a log:
    the build system's parting 'Build completed successfully' ends up after the
    test failure it preceded, and the tail of the record contradicts the
    verdict. One pipe keeps the child's own write order.
    """
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    return subprocess.run(cmd, shell=True, cwd=workdir, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def run_ladder(repo: Path, db, cfg: Config, mp_id: str) -> str | None:
    """Run gates bottom-up in a worktree of the merge result. Returns highest
    passed gate; queues test_fail on first failure."""
    mp = db.get_merge_point(mp_id)
    if not mp or not mp["result_commit"]:
        return None
    # A poisoned resolution is known-bad; scheduler.tick probes every point each tick,
    # so skip re-gating it (ready_targets/freeze already exclude poison from promotion).
    if mp["validation_state"] == "poison":
        return None
    base = mp["base_commit_sha"]
    highest = None

    # Check whether all gates are already cached passes — skip worktree if so.
    all_cached = all(
        db.gate_result(mp_id, gate["name"], base) == "pass"
        for gate in cfg.gates
    )
    if all_cached:
        return cfg.gates[-1]["name"] if cfg.gates else None

    with tempfile.TemporaryDirectory() as wt:
        gitio.git(repo, "worktree", "add", "--detach", wt, mp["result_commit"])
        try:
            for gate in cfg.gates:
                if db.gate_result(mp_id, gate["name"], base) == "pass":
                    highest = gate["name"]
                    continue
                state = db.get_merge_point(mp_id)["validation_state"]
                long_gate = bool(gate.get("long")) and state != "poison"
                if long_gate:
                    db.set_validation(mp_id, "inflight")
                cmd = gate["cmd"].replace("{workdir}", wt)
                proc = run_gate_cmd(cmd, wt)
                status = "pass" if proc.returncode == 0 else "fail"
                db.record_gate(mp_id, gate["name"], base, status)
                if status == "fail":
                    if long_gate:
                        db.set_validation(mp_id, "untested")
                    db.enqueue_work("test_fail", mp_id, _clip(proc.stdout),
                                    gate=gate["name"],
                                    exit_code=proc.returncode)
                    break
                # validation_state tracks the long-test verdict specifically, not the whole
                # ladder. If a later non-long gate fails it's surfaced via the queued test_fail
                # and via highest_gate (which stops at the failing rung), so promotion is gated
                # correctly without un-validating the long-test result.
                if long_gate:
                    db.set_validation(mp_id, "validated")
                highest = gate["name"]
        finally:
            gitio.git(repo, "worktree", "remove", "--force", wt, check=False)
    return highest


def ready_targets(db, cfg: Config, mp_id: str) -> list[str]:
    mp = db.get_merge_point(mp_id)
    if not mp:
        return []
    if mp["validation_state"] == "poison":
        return []
    highest = db.highest_gate(mp_id, mp["base_commit_sha"], cfg.ladder)
    if highest is None:
        return []
    reached = cfg.ladder.index(highest)
    return [t for t, req in cfg.targets.items()
            if cfg.ladder.index(req) <= reached]
