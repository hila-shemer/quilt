"""Configurable monotone gate ladder; results keyed by base commit (staleness =
absence of a row for the current base)."""
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import gitio


@dataclass
class Config:
    base: str
    branches: list[str]
    gates: list[dict]          # [{name, cmd}]
    targets: dict[str, str]    # target -> required gate

    @property
    def ladder(self):
        return [g["name"] for g in self.gates]


def load_config(path: Path) -> Config:
    raw = tomllib.loads(Path(path).read_text())
    return Config(base=raw["quilt"]["base"], branches=raw["quilt"]["branches"],
                  gates=raw.get("gate", []), targets=raw.get("targets", {}))


def run_ladder(repo: Path, db, cfg: Config, mp_id: str) -> str | None:
    """Run gates bottom-up in a worktree of the merge result. Returns highest
    passed gate; queues test_fail on first failure."""
    mp = db.get_merge_point(mp_id)
    if not mp or not mp["result_commit"]:
        return None
    base = mp["base_commit_sha"]
    highest = None
    with tempfile.TemporaryDirectory() as wt:
        gitio.git(repo, "worktree", "add", "--detach", wt, mp["result_commit"])
        try:
            for gate in cfg.gates:
                if db.gate_result(mp_id, gate["name"], base) == "pass":
                    highest = gate["name"]
                    continue
                cmd = gate["cmd"].format(workdir=wt)
                proc = subprocess.run(cmd, shell=True, cwd=wt,
                                      capture_output=True, text=True)
                status = "pass" if proc.returncode == 0 else "fail"
                db.record_gate(mp_id, gate["name"], base, status)
                if status == "fail":
                    db.enqueue_work("test_fail", mp_id,
                                    f"{gate['name']}: {proc.stdout[-1000:]}{proc.stderr[-1000:]}")
                    break
                highest = gate["name"]
        finally:
            gitio.git(repo, "worktree", "remove", "--force", wt, check=False)
    return highest


def ready_targets(db, cfg: Config, mp_id: str) -> list[str]:
    mp = db.get_merge_point(mp_id)
    highest = db.highest_gate(mp_id, mp["base_commit_sha"], cfg.ladder)
    if highest is None:
        return []
    reached = cfg.ladder.index(highest)
    return [t for t, req in cfg.targets.items()
            if cfg.ladder.index(req) <= reached]
