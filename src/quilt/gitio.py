"""Thin wrappers over git plumbing. All functions take repo path as first arg."""
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


def git(repo: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=check, capture_output=True, text=True).stdout.strip()


def rev(repo: Path, committish: str) -> str:
    return git(repo, "rev-parse", committish)


def tree_of(repo: Path, committish: str) -> str:
    return git(repo, "rev-parse", f"{committish}^{{tree}}")


def patch_id(repo: Path, base: str, tip: str) -> str:
    """Stable patch-id of the combined diff base..tip (metadata-insensitive)."""
    diff = subprocess.run(["git", "-C", str(repo), "diff", f"{base}...{tip}"],
                          check=True, capture_output=True, text=True).stdout
    out = subprocess.run(["git", "-C", str(repo), "patch-id", "--stable"],
                         input=diff, check=True, capture_output=True, text=True).stdout
    return out.split()[0] if out else ""


@dataclass
class MergeResult:
    clean: bool
    tree: str
    conflict_files: list[str] = field(default_factory=list)


def merge_tree(repo: Path, branch1: str, branch2: str) -> MergeResult:
    p = subprocess.run(
        ["git", "-C", str(repo), "merge-tree", "--write-tree",
         "--name-only", "--no-messages", branch1, branch2],
        capture_output=True, text=True)
    lines = p.stdout.splitlines()
    tree = lines[0].strip() if lines else ""
    if p.returncode == 0:
        return MergeResult(clean=True, tree=tree)
    return MergeResult(clean=False, tree=tree, conflict_files=lines[1:])


def commit_tree(repo: Path, tree: str, parents: list[str], msg: str) -> str:
    args = ["commit-tree", tree, "-m", msg]
    for p in parents:
        args += ["-p", p]
    return git(repo, *args)


def update_ref(repo: Path, ref: str, sha: str) -> None:
    git(repo, "update-ref", ref, sha)


def read_ref(repo: Path, ref: str) -> str | None:
    out = git(repo, "rev-parse", "--verify", "--quiet", ref, check=False)
    return out or None
