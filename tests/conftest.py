import subprocess
from pathlib import Path
import pytest


def make_stub(dirpath: Path, name: str, script: str) -> Path:
    """Write an executable stub script standing in for an LLM command."""
    p = dirpath / name
    p.write_text(script)
    p.chmod(0o755)
    return p


class Repo:
    """Wraps a real git repo for tests."""
    def __init__(self, path: Path):
        self.path = path

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def write(self, name: str, content: str) -> None:
        f = self.path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)

    def commit_file(self, name: str, content: str, msg: str | None = None) -> str:
        """Write file, commit, return commit SHA."""
        self.write(name, content)
        self.git("add", "-A")
        self.git("commit", "-m", msg or f"edit {name}")
        return self.git("rev-parse", "HEAD")

    def branch(self, name: str, at: str = "main") -> None:
        self.git("checkout", "-q", "-b", name, at)


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path / "repo")
    r.path.mkdir()
    r.git("init", "-q", "-b", "main")
    r.git("config", "user.email", "test@example.com")
    r.git("config", "user.name", "Test")
    r.commit_file("base.txt", "line1\nline2\nline3\n", "initial")
    return r


@pytest.fixture
def repo_with_branches(repo):
    """main + two feature branches: one touching a separate file (clean merge),
    one editing the same line (conflict)."""
    repo.branch("feat-clean")
    repo.commit_file("feature.txt", "new feature\n")
    repo.git("checkout", "-q", "main")
    repo.branch("feat-conflict")
    repo.commit_file("base.txt", "line1\nCONFLICT\nline3\n")
    repo.git("checkout", "-q", "main")
    return repo
