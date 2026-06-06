import pytest

from quilt.db import DB
from quilt.loom import epoch, schema
from quilt.loom.increments import Increment


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "q.sqlite3")
    schema.apply(d.conn)
    return d


def inc(id, patch="c"):
    return Increment(id=id, patches={"self": patch})


def test_mint_stable_for_same_set(db):
    incs = [inc("a"), inc("b")]
    e1 = epoch.mint(db, incs)
    e2 = epoch.mint(db, incs)          # same set → no roll
    assert e1 == e2 == epoch.current(db)


def test_set_change_rolls_epoch(db):
    e1 = epoch.mint(db, [inc("a")])
    e2 = epoch.mint(db, [inc("a"), inc("b")])   # added an increment
    assert e2 == e1 + 1


def test_patch_change_rolls_epoch(db):
    e1 = epoch.mint(db, [inc("a", "c1")])
    e2 = epoch.mint(db, [inc("a", "c2")])       # same id, different patch
    assert e2 == e1 + 1


def test_accept_current_rejects_stale(db):
    e0 = epoch.mint(db, [inc("a")])
    e1 = epoch.mint(db, [inc("a"), inc("b")])   # rolls → e0 now stale
    assert not epoch.accept(db, e0)             # stale agent result rejected
    assert epoch.accept(db, e1)                 # current accepted


def test_solve_stamps_epoch_on_solution(repo, tmp_path):
    # integration: a solved plan carries the epoch it was solved under
    from quilt import gates
    from quilt.loom import increments as inc_mod
    from quilt.loom import linearize
    from quilt.loom.worktree import WorktreePool
    d = DB(tmp_path / "q.sqlite3")
    schema.apply(d.conn)
    repo.git("checkout", "-q", "-b", "b-z", "main")
    sha = repo.commit_file("z.txt", "Z\n", "inc z")
    repo.git("checkout", "-q", "main")
    z = Increment(id="z", patches={"self": sha})
    inc_mod.add(d, z)
    c = gates.Config(base="main", branches=[],
                     gates=[{"name": "build", "test": False, "cmd": "true"}], targets={})
    sol = linearize.solve(repo.path, d, c, [z], WorktreePool(repo.path, root=tmp_path / "wt"))
    assert sol.epoch == epoch.current(d) >= 1
