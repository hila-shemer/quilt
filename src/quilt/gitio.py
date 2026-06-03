"""Thin wrappers over git plumbing. All functions take repo path as first arg."""
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class GitError(Exception):
    """Raised when a git command fails fatally or produces unexpected output."""


def _run(repo: Path, *args: str, input_text: str | None = None,
         check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command inside *repo* and return the CompletedProcess."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_text, check=check, capture_output=True, text=True,
    )


def git(repo: Path, *args: str, check: bool = True) -> str:
    return _run(repo, *args, check=check).stdout.strip()


def rev(repo: Path, committish: str) -> str:
    return git(repo, "rev-parse", committish)


def tree_of(repo: Path, committish: str) -> str:
    return git(repo, "rev-parse", f"{committish}^{{tree}}")


def patch_id(repo: Path, base: str, tip: str) -> str:
    """Stable patch-id of the combined diff base...tip (metadata-insensitive)."""
    diff = _run(repo, "diff", f"{base}...{tip}").stdout
    if not diff:
        raise GitError(f"empty diff: {base}...{tip}")
    out = _run(repo, "patch-id", "--stable", input_text=diff).stdout
    return out.split()[0] if out else ""


@dataclass
class MergeResult:
    clean: bool
    tree: str
    conflict_files: list[str] = field(default_factory=list)


def merge_tree(repo: Path, branch1: str, branch2: str) -> MergeResult:
    p = _run(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages",
             branch1, branch2, check=False)
    lines = p.stdout.splitlines()
    tree = lines[0].strip() if lines else ""
    if p.returncode == 0:
        return MergeResult(clean=True, tree=tree)
    # Distinguish conflict (tree written, stdout has content) from fatal error
    # (bad ref, missing object, etc. → stdout is empty).
    if not tree:
        raise GitError(
            f"git merge-tree failed fatally for '{branch1}' / '{branch2}': "
            + p.stderr.strip()
        )
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


def commit_patch_id(repo: Path, sha: str) -> str:
    """Stable patch-id of a single commit."""
    show = _run(repo, "show", sha).stdout
    out = _run(repo, "patch-id", "--stable", input_text=show).stdout
    return out.split()[0] if out else ""


def patch_ids_of_range(repo: Path, base: str, tip: str) -> set[str]:
    """Stable patch-ids of every commit in base..tip."""
    log = _run(repo, "log", "-p", f"{base}..{tip}").stdout
    out = _run(repo, "patch-id", "--stable", input_text=log).stdout
    return {line.split()[0] for line in out.splitlines() if line.strip()}
