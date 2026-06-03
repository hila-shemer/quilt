# Quilt Integration Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone CLI tool (`quilt`) that probes all combinations of ≤5 feature branches against a base, runs a monotone test-gate ladder over them, caches resolutions as git refs, and promotes proven combinations.

**Architecture:** Python package wrapping git plumbing (`merge-tree --write-tree`, `patch-id --stable`, `commit-tree`) with a SQLite database keyed by content (base tree + member patch-ids). Deterministic core only — LLM steps (triage, semantic conflict resolution) materialize as work-queue rows for later phases.

**Tech Stack:** Python 3.12 stdlib (sqlite3, tomllib, subprocess), pytest, git ≥2.40, git-mediate.

**JIRA:** TBD (user to open ticket).

Repository: `/home/shemer/quilt` (this repo). Design spec: `mds/quilt-design.md`.

---

### File structure

```
quilt/
  pyproject.toml
  README.md
  src/quilt/__init__.py
  src/quilt/gitio.py        # git plumbing wrappers: run(), patch_id, merge_tree, commit_tree, refs
  src/quilt/keys.py         # merge-point identity: hash(base_tree, sorted patch-ids)
  src/quilt/db.py           # SQLite schema + CRUD
  src/quilt/probe.py        # power-set enumeration + parallel merge-tree probe
  src/quilt/resolve.py      # git-mediate resolution + refs/quilt pinning + poison cascade
  src/quilt/gates.py        # gate ladder runners + promotion readiness
  src/quilt/scheduler.py    # happy-path tick + agent work queue
  src/quilt/cli.py          # argparse CLI
  tests/conftest.py         # temp git repo factory
  tests/test_gitio.py
  tests/test_keys.py
  tests/test_db.py
  tests/test_probe.py
  tests/test_resolve.py
  tests/test_gates.py
  tests/test_scheduler.py
```

All tests use real temp git repos. No mocks.

---

### Task 1: Project skeleton

**Files:** Create `pyproject.toml`, `src/quilt/__init__.py`, `tests/__init__.py` (empty), `.gitignore`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "quilt"
version = "0.1.0"
description = "Branch-combination integration pipeline"
requires-python = ">=3.12"

[project.scripts]
quilt = "quilt.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Write `src/quilt/__init__.py`** — `__version__ = "0.1.0"`.

- [ ] **Step 3: Write `.gitignore`** — `__pycache__/`, `*.egg-info/`, `.venv/`, `*.sqlite3`.

- [ ] **Step 4: Create venv, install editable, verify**

Run: `cd /home/shemer/quilt && python3 -m venv .venv && .venv/bin/pip install -e . pytest -q && .venv/bin/python -c "import quilt; print(quilt.__version__)"`
Expected: `0.1.0`

- [ ] **Step 5: Commit** `chore: project skeleton`

---

### Task 2: Test fixture — temp git repo factory

**Files:** Create `tests/conftest.py`

- [ ] **Step 1: Write fixture**

```python
import subprocess
from pathlib import Path
import pytest


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
```

- [ ] **Step 2: Smoke-test the fixture** — add `tests/test_conftest.py`:

```python
def test_fixture_branches(repo_with_branches):
    r = repo_with_branches
    assert set(r.git("branch", "--format=%(refname:short)").splitlines()) == {
        "main", "feat-clean", "feat-conflict",
    }
```

Run: `.venv/bin/pytest tests/test_conftest.py -v` — expected: PASS.

- [ ] **Step 3: Commit** `test: temp git repo fixture`

---

### Task 3: gitio — git plumbing wrappers

**Files:** Create `src/quilt/gitio.py`, `tests/test_gitio.py`

API:

```python
git(repo, *args, check=True) -> str                  # run git, return stdout
patch_id(repo, base, tip) -> str                     # stable patch-id of base..tip
tree_of(repo, committish) -> str                     # rev-parse <c>^{tree}
merge_tree(repo, c1, c2) -> MergeResult              # merge-tree --write-tree
commit_tree(repo, tree, parents, msg) -> str
update_ref(repo, ref, sha) -> None
read_ref(repo, ref) -> str | None
```

- [ ] **Step 1: Write failing tests**

```python
import subprocess
import pytest
from quilt import gitio


def test_patch_id_stable_across_metadata(repo_with_branches):
    r = repo_with_branches
    pid1 = gitio.patch_id(r.path, "main", "feat-clean")
    # Recommit feat-clean with a different message/date — patch-id must not move.
    r.git("checkout", "-q", "feat-clean")
    r.git("commit", "--amend", "-m", "different message",
          "--date", "2001-01-01T00:00:00")
    pid2 = gitio.patch_id(r.path, "main", "feat-clean")
    assert pid1 == pid2


def test_merge_tree_clean(repo_with_branches):
    res = gitio.merge_tree(repo_with_branches.path, "feat-clean", "main")
    assert res.clean
    assert res.tree


def test_merge_tree_conflict(repo_with_branches):
    r = repo_with_branches
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")
    res = gitio.merge_tree(r.path, "feat-conflict", "main")
    assert not res.clean
    assert "base.txt" in res.conflict_files


def test_commit_tree_and_refs(repo_with_branches):
    r = repo_with_branches
    res = gitio.merge_tree(r.path, "feat-clean", "main")
    sha = gitio.commit_tree(r.path, res.tree,
                            parents=[gitio.rev(r.path, "main"),
                                     gitio.rev(r.path, "feat-clean")],
                            msg="merge")
    gitio.update_ref(r.path, "refs/quilt/test", sha)
    assert gitio.read_ref(r.path, "refs/quilt/test") == sha
    assert gitio.read_ref(r.path, "refs/quilt/missing") is None
```

Run: `.venv/bin/pytest tests/test_gitio.py -v` — expected: FAIL (module missing).

- [ ] **Step 2: Implement**

```python
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
```

- [ ] **Step 3: Run tests** — expected: 4 PASS.

- [ ] **Step 4: Commit** `feat: git plumbing wrappers`

---

### Task 4: keys — content-keyed merge-point identity

**Files:** Create `src/quilt/keys.py`, `tests/test_keys.py`

- [ ] **Step 1: Failing tests**

```python
from quilt import keys


def test_id_is_order_insensitive():
    a = keys.merge_point_id("treesha", ["p2", "p1"])
    b = keys.merge_point_id("treesha", ["p1", "p2"])
    assert a == b


def test_id_changes_with_base_and_members():
    a = keys.merge_point_id("tree1", ["p1"])
    assert a != keys.merge_point_id("tree2", ["p1"])
    assert a != keys.merge_point_id("tree1", ["p1", "p2"])
    assert len(a) == 64
```

- [ ] **Step 2: Implement**

```python
"""Merge-point identity: content-keyed, never branch names."""
import hashlib


def merge_point_id(base_tree_sha: str, member_patch_ids: list[str]) -> str:
    canon = base_tree_sha + ":" + ",".join(sorted(member_patch_ids))
    return hashlib.sha256(canon.encode()).hexdigest()
```

- [ ] **Step 3: Run, expect 2 PASS. Commit** `feat: content-keyed merge-point identity`

---

### Task 5: db — schema + CRUD

**Files:** Create `src/quilt/db.py`, `tests/test_db.py`

- [ ] **Step 1: Failing tests**

```python
import pytest
from quilt.db import DB


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "quilt.sqlite3")


def test_merge_point_roundtrip(db):
    db.upsert_merge_point(id="mp1", base_tree_sha="t", base_commit_sha="c",
                          member_patch_ids=["p1", "p2"], member_tips=["s1", "s2"],
                          construction="clean")
    mp = db.get_merge_point("mp1")
    assert mp["construction"] == "clean"
    assert mp["validation_state"] == "untested"
    assert mp["member_patch_ids"] == ["p1", "p2"]


def test_gate_status_keyed_by_base(db):
    db.upsert_merge_point(id="mp1", base_tree_sha="t", base_commit_sha="c1",
                          member_patch_ids=["p1"], member_tips=["s1"],
                          construction="clean")
    db.record_gate("mp1", "compiles", "c1", "pass")
    assert db.gate_result("mp1", "compiles", "c1") == "pass"
    # staleness = absence of row for the new base
    assert db.gate_result("mp1", "compiles", "c2") is None


def test_highest_gate_derived(db):
    db.upsert_merge_point(id="mp1", base_tree_sha="t", base_commit_sha="c1",
                          member_patch_ids=["p1"], member_tips=["s1"],
                          construction="clean")
    db.record_gate("mp1", "compiles", "c1", "pass")
    db.record_gate("mp1", "triton", "c1", "pass")
    db.record_gate("mp1", "mllib_ut", "c1", "fail")
    assert db.highest_gate("mp1", "c1", ["compiles", "triton", "mllib_ut", "t4h"]) == "triton"


def test_poison_cascades_to_supersets(db):
    for id_, members in [("a", ["p1"]), ("ab", ["p1", "p2"]), ("b", ["p2"])]:
        db.upsert_merge_point(id=id_, base_tree_sha="t", base_commit_sha="c",
                              member_patch_ids=members, member_tips=members,
                              construction="clean")
    db.set_validation("a", "poison")
    assert db.get_merge_point("ab")["validation_state"] == "untested"   # reset
    assert db.get_merge_point("b")["validation_state"] == "untested"    # untouched
    assert db.get_merge_point("a")["validation_state"] == "poison"
```

- [ ] **Step 2: Implement**

```python
"""SQLite store. The DB is the source of truth about combinations; git only
stores trees and refs. member_patch_ids stored sorted comma-joined."""
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS merge_point (
  id               TEXT PRIMARY KEY,
  base_tree_sha    TEXT NOT NULL,
  base_commit_sha  TEXT NOT NULL,
  member_patch_ids TEXT NOT NULL,
  member_tips      TEXT NOT NULL,
  result_commit    TEXT,
  result_tree      TEXT,
  construction     TEXT NOT NULL,
  validation_state TEXT NOT NULL DEFAULT 'untested',
  created_at       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS gate_status (
  merge_point_id  TEXT NOT NULL REFERENCES merge_point(id),
  gate            TEXT NOT NULL,
  base_commit_sha TEXT NOT NULL,
  status          TEXT NOT NULL,
  result_ref      TEXT,
  started_at      INTEGER,
  finished_at     INTEGER,
  PRIMARY KEY (merge_point_id, gate, base_commit_sha)
);
CREATE TABLE IF NOT EXISTS frankenmerge_fix (
  merge_point_id TEXT NOT NULL REFERENCES merge_point(id),
  patch_ref      TEXT NOT NULL,
  affected_tips  TEXT NOT NULL,
  backprop_state TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS triage (
  id           TEXT PRIMARY KEY,
  target_id    TEXT NOT NULL,
  kind         TEXT NOT NULL,
  est_cause    TEXT,
  effort_class TEXT NOT NULL,
  model        TEXT,
  created_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS work_queue (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT NOT NULL,         -- conflict | test_fail
  target_id  TEXT NOT NULL,
  detail     TEXT,
  state      TEXT NOT NULL DEFAULT 'queued',  -- queued | done | dropped
  created_at INTEGER NOT NULL
);
"""


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def upsert_merge_point(self, *, id, base_tree_sha, base_commit_sha,
                           member_patch_ids, member_tips, construction,
                           result_commit=None, result_tree=None):
        self.conn.execute(
            """INSERT INTO merge_point (id, base_tree_sha, base_commit_sha,
                 member_patch_ids, member_tips, result_commit, result_tree,
                 construction, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 base_commit_sha=excluded.base_commit_sha,
                 member_tips=excluded.member_tips,
                 result_commit=excluded.result_commit,
                 result_tree=excluded.result_tree,
                 construction=excluded.construction""",
            (id, base_tree_sha, base_commit_sha,
             ",".join(sorted(member_patch_ids)), json.dumps(member_tips),
             result_commit, result_tree, construction, int(time.time())))
        self.conn.commit()

    def get_merge_point(self, id):
        row = self.conn.execute("SELECT * FROM merge_point WHERE id=?", (id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["member_patch_ids"] = d["member_patch_ids"].split(",")
        d["member_tips"] = json.loads(d["member_tips"])
        return d

    def list_merge_points(self, base_tree_sha=None):
        q, args = "SELECT id FROM merge_point", ()
        if base_tree_sha:
            q += " WHERE base_tree_sha=?"
            args = (base_tree_sha,)
        return [self.get_merge_point(r["id"]) for r in self.conn.execute(q, args)]

    def record_gate(self, mp_id, gate, base_sha, status, result_ref=None):
        now = int(time.time())
        self.conn.execute(
            """INSERT INTO gate_status VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(merge_point_id, gate, base_commit_sha)
               DO UPDATE SET status=excluded.status, result_ref=excluded.result_ref,
                             finished_at=excluded.finished_at""",
            (mp_id, gate, base_sha, status, result_ref, now, now))
        self.conn.commit()

    def gate_result(self, mp_id, gate, base_sha):
        row = self.conn.execute(
            "SELECT status FROM gate_status WHERE merge_point_id=? AND gate=? AND base_commit_sha=?",
            (mp_id, gate, base_sha)).fetchone()
        return row["status"] if row else None

    def highest_gate(self, mp_id, base_sha, ladder):
        highest = None
        for gate in ladder:
            if self.gate_result(mp_id, gate, base_sha) != "pass":
                break
            highest = gate
        return highest

    def set_validation(self, mp_id, state):
        self.conn.execute("UPDATE merge_point SET validation_state=? WHERE id=?",
                          (state, mp_id))
        if state == "poison":
            poisoned = self.get_merge_point(mp_id)
            members = set(poisoned["member_patch_ids"])
            for mp in self.list_merge_points():
                if mp["id"] != mp_id and members < set(mp["member_patch_ids"]):
                    self.conn.execute(
                        "UPDATE merge_point SET validation_state='untested' WHERE id=?",
                        (mp["id"],))
        self.conn.commit()

    def enqueue_work(self, kind, target_id, detail=""):
        self.conn.execute(
            "INSERT INTO work_queue (kind, target_id, detail, created_at) VALUES (?,?,?,?)",
            (kind, target_id, detail, int(time.time())))
        self.conn.commit()

    def pending_work(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM work_queue WHERE state='queued' ORDER BY id")]
```

- [ ] **Step 3: Run tests, expect 4 PASS. Commit** `feat: sqlite store with content-keyed gate ladder and poison cascade`

---

### Task 6: probe — power-set enumeration + merge probe

**Files:** Create `src/quilt/probe.py`, `tests/test_probe.py`

- [ ] **Step 1: Failing tests**

```python
import pytest
from quilt.db import DB
from quilt import probe


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "q.sqlite3")


def test_rejects_more_than_five():
    with pytest.raises(ValueError):
        probe.enumerate_combos(["b1", "b2", "b3", "b4", "b5", "b6"])


def test_enumerates_power_set():
    combos = probe.enumerate_combos(["a", "b", "c"])
    assert len(combos) == 7  # 2^3 - 1


def test_probe_records_clean_and_conflict(repo_with_branches, db):
    results = probe.probe_all(repo_with_branches.path, "main",
                              ["feat-clean", "feat-conflict"], db)
    assert len(results) == 3
    by_members = {tuple(sorted(r["branches"])): r for r in results}
    assert by_members[("feat-clean",)]["construction"] == "clean"
    assert by_members[("feat-clean", "feat-conflict")]["construction"] == "clean"
    mp = db.get_merge_point(results[0]["id"])
    assert mp is not None


def test_probe_marks_conflicts(repo_with_branches, db):
    r = repo_with_branches
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")
    results = probe.probe_all(r.path, "main", ["feat-clean", "feat-conflict"], db)
    by_members = {tuple(sorted(x["branches"])): x for x in results}
    assert by_members[("feat-conflict",)]["construction"] == "conflict"
    assert by_members[("feat-clean",)]["construction"] == "clean"
```

- [ ] **Step 2: Implement**

```python
"""Enumerate branch combinations (N<=5) and probe each with merge-tree.
Sequential pairwise merges; conflict at any step marks the combo 'conflict'."""
from itertools import combinations
from pathlib import Path

from . import gitio
from .keys import merge_point_id

MAX_BRANCHES = 5


def enumerate_combos(branches: list[str]) -> list[tuple[str, ...]]:
    if len(branches) > MAX_BRANCHES:
        raise ValueError(f"more than {MAX_BRANCHES} branches; promote one first")
    out = []
    for k in range(1, len(branches) + 1):
        out.extend(combinations(branches, k))
    return out


def probe_combo(repo: Path, base: str, branches: tuple[str, ...]):
    """Merge branches into base sequentially via merge-tree; returns
    (construction, result_commit, result_tree)."""
    current = gitio.rev(repo, base)
    for branch in sorted(branches, key=lambda b: gitio.patch_id(repo, base, b)):
        res = gitio.merge_tree(repo, current, branch)
        if not res.clean:
            return "conflict", None, None
        current = gitio.commit_tree(repo, res.tree,
                                    parents=[current, gitio.rev(repo, branch)],
                                    msg=f"quilt: merge {branch}")
    return "clean", current, gitio.tree_of(repo, current)


def probe_all(repo: Path, base: str, branches: list[str], db) -> list[dict]:
    base_tree = gitio.tree_of(repo, base)
    base_commit = gitio.rev(repo, base)
    pids = {b: gitio.patch_id(repo, base, b) for b in branches}
    results = []
    for combo in enumerate_combos(branches):
        member_pids = [pids[b] for b in combo]
        mp_id = merge_point_id(base_tree, member_pids)
        construction, commit, tree = probe_combo(repo, base, combo)
        db.upsert_merge_point(
            id=mp_id, base_tree_sha=base_tree, base_commit_sha=base_commit,
            member_patch_ids=member_pids,
            member_tips=[gitio.rev(repo, b) for b in combo],
            construction=construction, result_commit=commit, result_tree=tree)
        if commit:
            gitio.update_ref(repo, f"refs/quilt/{mp_id}", commit)
        results.append({"id": mp_id, "branches": list(combo),
                        "construction": construction})
    return results
```

- [ ] **Step 3: Run tests, expect 4 PASS. Commit** `feat: power-set merge probe`

---

### Task 7: resolve — git-mediate + resolution reuse + poison gate

**Files:** Create `src/quilt/resolve.py`, `tests/test_resolve.py`

Behavior: take a conflicted merge-point, materialize a temp worktree, run the merge, run `git-mediate`. If all conflicts clear → commit, pin `refs/quilt/<id>`, set construction `mediated`. Else leave a `conflict` work-queue row. Reuse only when not poison.

- [ ] **Step 1: Failing tests**

```python
import pytest
from quilt.db import DB
from quilt import gitio, probe, resolve


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "q.sqlite3")


@pytest.fixture
def conflicted(repo_with_branches, db):
    r = repo_with_branches
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")
    results = probe.probe_all(r.path, "main", ["feat-conflict"], db)
    assert results[0]["construction"] == "conflict"
    return r, db, results[0]["id"]


def test_mediate_fails_on_semantic_conflict(conflicted):
    repo, db, mp_id = conflicted
    out = resolve.try_mediate(repo.path, "main", db, mp_id)
    assert out is None                      # mediate can't fix both-edited line
    assert db.pending_work()[0]["kind"] == "conflict"


def test_mediate_resolves_one_side_unchanged(repo, db):
    # feat changes the line; main is unchanged after branch point -> trivial.
    repo.branch("feat")
    repo.commit_file("base.txt", "line1\nEDIT\nline3\n")
    repo.git("checkout", "-q", "main")
    repo.commit_file("other.txt", "x\n")
    res = probe.probe_all(repo.path, "main", ["feat"], db)
    assert res[0]["construction"] == "clean"  # not even a conflict


def test_reuse_blocked_when_poison(conflicted):
    repo, db, mp_id = conflicted
    db.set_validation(mp_id, "poison")
    assert resolve.reusable_resolution(repo.path, db, mp_id) is None


def test_reuse_returns_pinned_ref(repo_with_branches, db):
    res = probe.probe_all(repo_with_branches.path, "main", ["feat-clean"], db)
    mp_id = res[0]["id"]
    sha = resolve.reusable_resolution(repo_with_branches.path, db, mp_id)
    assert sha == gitio.read_ref(repo_with_branches.path, f"refs/quilt/{mp_id}")
```

- [ ] **Step 2: Implement**

```python
"""Resolution layer: pinned refs + git-mediate for trivial conflicts.
Reuse is blocked only by validation_state == 'poison'."""
import subprocess
import tempfile
from pathlib import Path

from . import gitio


def reusable_resolution(repo: Path, db, mp_id: str) -> str | None:
    mp = db.get_merge_point(mp_id)
    if mp is None or mp["validation_state"] == "poison":
        return None
    return gitio.read_ref(repo, f"refs/quilt/{mp_id}")


def try_mediate(repo: Path, base: str, db, mp_id: str) -> str | None:
    """Try resolving a conflicted merge-point with git-mediate in a temp
    worktree. Returns merge commit SHA, or None (queues agent work)."""
    mp = db.get_merge_point(mp_id)
    tips = mp["member_tips"]
    with tempfile.TemporaryDirectory() as wt:
        gitio.git(repo, "worktree", "add", "--detach", wt, base)
        try:
            wt_path = Path(wt)
            merged = gitio.rev(repo, base)
            for tip in tips:
                p = subprocess.run(["git", "-C", wt, "merge", "--no-ff", tip],
                                   capture_output=True, text=True)
                if p.returncode == 0:
                    continue
                m = subprocess.run(["git-mediate"], cwd=wt,
                                   capture_output=True, text=True)
                if m.returncode != 0:
                    db.enqueue_work("conflict", mp_id, m.stdout[-2000:])
                    return None
                subprocess.run(["git", "-C", wt, "commit", "-am",
                                f"quilt: mediated merge {tip}"],
                               check=True, capture_output=True)
            merged = gitio.rev(wt_path, "HEAD")
            gitio.update_ref(repo, f"refs/quilt/{mp_id}", merged)
            db.upsert_merge_point(
                id=mp_id, base_tree_sha=mp["base_tree_sha"],
                base_commit_sha=mp["base_commit_sha"],
                member_patch_ids=mp["member_patch_ids"],
                member_tips=tips, construction="mediated",
                result_commit=merged, result_tree=gitio.tree_of(repo, merged))
            return merged
        finally:
            gitio.git(repo, "worktree", "remove", "--force", wt, check=False)
```

- [ ] **Step 3: Run tests, expect 4 PASS. Commit** `feat: mediate resolution + poison-gated reuse`

---

### Task 8: gates — configurable ladder + readiness

**Files:** Create `src/quilt/gates.py`, `tests/test_gates.py`

Config file `quilt.toml` at tool-user level:

```toml
[quilt]
base = "main"
branches = ["feat-a", "feat-b"]

[[gate]]
name = "compiles"
cmd = "make -C {workdir} build"

[[gate]]
name = "fast_tests"
cmd = "make -C {workdir} test"

[targets]
next = "compiles"
local-stable = "fast_tests"
```

- [ ] **Step 1: Failing tests**

```python
import pytest
from quilt.db import DB
from quilt import gates, probe

CFG = """
[quilt]
base = "main"
branches = ["feat-clean"]

[[gate]]
name = "compiles"
cmd = "test -f base.txt"

[[gate]]
name = "fast_tests"
cmd = "test -f feature.txt"

[targets]
next = "compiles"
local-stable = "fast_tests"
"""


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "quilt.toml"
    p.write_text(CFG)
    return gates.load_config(p)


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "q.sqlite3")


def test_ladder_order(cfg):
    assert cfg.ladder == ["compiles", "fast_tests"]


def test_run_gates_records_results(repo_with_branches, db, cfg):
    [mp] = probe.probe_all(repo_with_branches.path, "main", ["feat-clean"], db)
    passed = gates.run_ladder(repo_with_branches.path, db, cfg, mp["id"])
    assert passed == "fast_tests"
    base = db.get_merge_point(mp["id"])["base_commit_sha"]
    assert db.gate_result(mp["id"], "compiles", base) == "pass"


def test_ladder_stops_at_failure(repo_with_branches, db, cfg, tmp_path):
    [mp] = probe.probe_all(repo_with_branches.path, "main", ["feat-conflict"], db)
    # feat-conflict lacks feature.txt -> fast_tests fails, queued for triage
    passed = gates.run_ladder(repo_with_branches.path, db, cfg, mp["id"])
    assert passed == "compiles"
    assert db.pending_work()[0]["kind"] == "test_fail"


def test_ready_targets(repo_with_branches, db, cfg):
    [mp] = probe.probe_all(repo_with_branches.path, "main", ["feat-clean"], db)
    gates.run_ladder(repo_with_branches.path, db, cfg, mp["id"])
    assert gates.ready_targets(db, cfg, mp["id"]) == ["next", "local-stable"]
```

- [ ] **Step 2: Implement**

```python
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
```

- [ ] **Step 3: Run tests, expect 4 PASS. Commit** `feat: configurable gate ladder + readiness`

---

### Task 9: scheduler — happy-path tick + K-bound

**Files:** Create `src/quilt/scheduler.py`, `tests/test_scheduler.py`

One `tick()` = probe → mediate conflicts → reuse resolutions → run ladders, with K-bound on untested resolutions sharing a member subset.

- [ ] **Step 1: Failing tests**

```python
import pytest
from quilt.db import DB
from quilt import gates, scheduler

CFG = """
[quilt]
base = "main"
branches = ["feat-clean", "feat-conflict"]

[[gate]]
name = "compiles"
cmd = "test -f base.txt"

[targets]
next = "compiles"
"""


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "quilt.toml"
    p.write_text(CFG)
    return gates.load_config(p)


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "q.sqlite3")


def test_tick_happy_path(repo_with_branches, db, cfg):
    report = scheduler.tick(repo_with_branches.path, db, cfg)
    # 3 combos; all merge clean; gates run on each
    assert report["probed"] == 3
    assert report["gated"] == 3
    assert report["queued"] == 0


def test_tick_routes_conflicts(repo_with_branches, db, cfg):
    r = repo_with_branches
    r.git("checkout", "-q", "main")
    r.commit_file("base.txt", "line1\nMAIN\nline3\n")
    report = scheduler.tick(r.path, db, cfg)
    assert report["queued"] == 2          # both combos containing feat-conflict
    assert report["gated"] == 1


def test_unvalidated_serialization(db, tmp_path, repo_with_branches):
    # Three clean branches -> pairs {a,b} and {a,c} share untested subset {a}.
    r = repo_with_branches
    r.branch("feat-clean2")
    r.commit_file("feature2.txt", "another feature\n")
    r.git("checkout", "-q", "main")
    cfg3 = tmp_path / "q3.toml"
    cfg3.write_text(CFG.replace(
        '["feat-clean", "feat-conflict"]',
        '["feat-clean", "feat-clean2", "feat-conflict"]'))
    report = scheduler.tick(r.path, db, gates.load_config(cfg3), heavy_k=1)
    assert report["deferred"] >= 1        # one heavy slot per untested resolution
```

- [ ] **Step 2: Implement**

```python
"""One scheduler tick: deterministic, cheapest-first; agents only via queue."""
from pathlib import Path

from . import gates as gates_mod
from . import probe, resolve


def tick(repo: Path, db, cfg, heavy_k: int = 1) -> dict:
    report = {"probed": 0, "gated": 0, "queued": 0, "deferred": 0}
    results = probe.probe_all(repo, cfg.base, cfg.branches, db)
    report["probed"] = len(results)

    heavy_used: dict[str, int] = {}   # smallest untested subset id -> count
    for r in results:
        mp_id = r["id"]
        if r["construction"] == "conflict":
            if resolve.reusable_resolution(repo, db, mp_id) is None and \
               resolve.try_mediate(repo, cfg.base, db, mp_id) is None:
                report["queued"] += 1
                continue
        mp = db.get_merge_point(mp_id)
        if mp["validation_state"] == "untested" and len(mp["member_patch_ids"]) > 1:
            key = ",".join(mp["member_patch_ids"][:-1])
            if heavy_used.get(key, 0) >= heavy_k:
                report["deferred"] += 1
                continue
            heavy_used[key] = heavy_used.get(key, 0) + 1
        gates_mod.run_ladder(repo, db, cfg, mp_id)
        report["gated"] += 1
    return report
```

- [ ] **Step 3: Run tests, expect 3 PASS. Commit** `feat: scheduler tick with K-bounded heavy slots`

---

### Task 10: CLI

**Files:** Create `src/quilt/cli.py`, `tests/test_cli.py`

Commands: `quilt probe`, `quilt tick`, `quilt status`, `quilt promote <target>`, `quilt queue`. Global flags: `--repo`, `--config` (default `quilt.toml`), `--db` (default `.quilt.sqlite3` beside config).

- [ ] **Step 1: Failing tests**

```python
import pytest
from quilt import cli

CFG = """
[quilt]
base = "main"
branches = ["feat-clean", "feat-conflict"]

[[gate]]
name = "compiles"
cmd = "test -f base.txt"

[targets]
next = "compiles"
"""


@pytest.fixture
def cfgfile(tmp_path):
    p = tmp_path / "quilt.toml"
    p.write_text(CFG)
    return p


def run(args, repo, cfgfile, capsys):
    cli.main(["--repo", str(repo.path), "--config", str(cfgfile)] + args)
    return capsys.readouterr().out


def test_tick_and_status(repo_with_branches, cfgfile, capsys):
    run(["tick"], repo_with_branches, cfgfile, capsys)
    out = run(["status"], repo_with_branches, cfgfile, capsys)
    assert "clean" in out
    assert "compiles" in out


def test_promote_advances_target(repo_with_branches, cfgfile, capsys):
    run(["tick"], repo_with_branches, cfgfile, capsys)
    out = run(["promote", "next"], repo_with_branches, cfgfile, capsys)
    assert "next" in out
    assert repo_with_branches.git("rev-parse", "refs/quilt/target/next")
```

- [ ] **Step 2: Implement**

```python
"""quilt CLI."""
import argparse
from pathlib import Path

from . import gates as gates_mod
from . import gitio, probe, scheduler
from .db import DB


def main(argv=None):
    ap = argparse.ArgumentParser(prog="quilt")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--config", default="quilt.toml")
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    sub.add_parser("tick")
    sub.add_parser("status")
    sub.add_parser("queue")
    p = sub.add_parser("promote")
    p.add_argument("target")
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    cfg = gates_mod.load_config(Path(args.config))
    db = DB(Path(args.db) if args.db else Path(args.config).parent / ".quilt.sqlite3")

    if args.cmd == "probe":
        for r in probe.probe_all(repo, cfg.base, cfg.branches, db):
            print(f"{r['id'][:12]} {r['construction']:9} {'+'.join(r['branches'])}")
    elif args.cmd == "tick":
        print(scheduler.tick(repo, db, cfg))
    elif args.cmd == "status":
        for mp in db.list_merge_points(gitio.tree_of(repo, cfg.base)):
            highest = db.highest_gate(mp["id"], mp["base_commit_sha"], cfg.ladder)
            print(f"{mp['id'][:12]} {mp['construction']:9} "
                  f"{mp['validation_state']:9} gate={highest or '-'}")
    elif args.cmd == "queue":
        for w in db.pending_work():
            print(f"{w['id']:4} {w['kind']:10} {w['target_id'][:12]} {w['detail'][:60]}")
    elif args.cmd == "promote":
        required = cfg.targets[args.target]
        for mp in db.list_merge_points(gitio.tree_of(repo, cfg.base)):
            if args.target in gates_mod.ready_targets(db, cfg, mp["id"]) \
               and mp["result_commit"]:
                gitio.update_ref(repo, f"refs/quilt/target/{args.target}",
                                 mp["result_commit"])
                print(f"{args.target} -> {mp['result_commit'][:12]} "
                      f"(gate {required}, {len(mp['member_patch_ids'])} members)")
                return
        print(f"no merge-point ready for {args.target}")


if __name__ == "__main__":
    main()
```

Promotion picks the *largest ready* merge-point (most members). Sort candidates by `len(member_patch_ids)` descending before picking.

- [ ] **Step 3: Run, expect PASS. Commit** `feat: quilt CLI (probe/tick/status/queue/promote)`

---

### Task 11: full suite + README

- [ ] **Step 1: Run full suite** — `.venv/bin/pytest -q` — expect ~25 PASS.
- [ ] **Step 2: Write README.md** — overview (what/why), `pip install -e .`, quickstart with `quilt.toml` example (use the gate config example from Task 8), gate ladder semantics, pointer to `mds/quilt-design.md` for design.
- [ ] **Step 3: Run a real-life smoke test** — point `quilt probe --repo /home/shemer/singlehex` at a config listing two real branches and confirm it reports `clean`/`conflict` per combo.
- [ ] **Step 4: Commit** `docs: README`

