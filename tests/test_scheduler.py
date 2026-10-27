import pytest
from quilt.db import DB
from quilt import candidate, gates, scheduler

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


CFG_PROMO = CFG + """
[promotion]
target = "main"
candidate_gate = "compiles"
final_gate = "t4day"
final_cmd = "test -f base.txt"
"""

# compiles passes and tests does not, so a clean merge is not a pass: the
# unsoundness the short-circuit must not be built on.
CFG_TWO_GATE = """
[quilt]
base = "main"
branches = ["feat-clean", "feat-conflict"]

[[gate]]
name = "compiles"
cmd = "test -f base.txt"

[[gate]]
name = "tests"
cmd = "false"

[targets]
main = "tests"

[promotion]
target = "main"
candidate_gate = "tests"
final_gate = "promote"
final_cmd = "test -f base.txt"
"""


def _cfg_at(tmp_path, text, name="q.toml"):
    p = tmp_path / name
    p.write_text(text)
    return gates.load_config(p)


def test_tick_happy_path(repo_with_branches, db, cfg):
    report = scheduler.tick(repo_with_branches.path, db, cfg)
    # No [promotion] section, so no candidate gate: nothing to short-circuit
    # on, and today's 3 combos are probed and gated as they always were.
    assert report["probed"] == 3
    assert report["gated"] == 3
    assert report["queued"] == 0
    assert report["short_circuit"] is False


def test_tick_short_circuits_when_the_full_set_clears_the_candidate_gate(
        repo_with_branches, db, tmp_path):
    """The size-N set clears the gate, so the other 2^N-2 are never probed.

    Every other combination is a strict subset of it, and `freeze` picks the
    largest ready merge-point: the full set is the unique maximum, so no subset
    probe can tie or beat it and none of them is worth the merge or the gate.
    """
    r = repo_with_branches
    cfg = _cfg_at(tmp_path, CFG_PROMO)
    report = scheduler.tick(r.path, db, cfg)
    assert report["short_circuit"] is True
    assert report["probed"] == 1
    assert report["gated"] == 1
    # the skip is real: no subset row was ever recorded
    assert [mp["member_branches"] for mp in db.list_merge_points()] == \
        [["feat-clean", "feat-conflict"]]


def test_the_pool_is_idle_after_the_short_circuit(repo_with_branches, db, tmp_path):
    """The short-circuit's other half, and the bug it would otherwise be:
    `schedulable` re-enumerates the power set, so the 2 unprobed subsets of this
    pool read as pending forever and `quilt idle` never says idle — the tick
    and the idle predicate have to agree about a set that was never probed."""
    r = repo_with_branches
    cfg = _cfg_at(tmp_path, CFG_PROMO)
    assert scheduler.tick(r.path, db, cfg)["short_circuit"] is True
    assert len(db.list_merge_points()) == 1     # the 2 singles were never probed
    assert scheduler.schedulable(r.path, db, cfg) == []


def test_tick_falls_back_when_the_full_set_fails_the_candidate_gate(
        repo_with_branches, db, tmp_path):
    """Merging clean is not passing. The full set merges clean and clears
    `compiles`, but the candidate gate is `tests` and it fails it — so the tick
    must walk the whole power set exactly as before, and the subset rows must be
    there for the poison cascade and the failure bisect to use."""
    r = repo_with_branches
    cfg = _cfg_at(tmp_path, CFG_TWO_GATE)
    report = scheduler.tick(r.path, db, cfg)
    assert report["short_circuit"] is False
    assert report["probed"] == 3
    assert report["gated"] == 3          # each row cleared `compiles`
    assert len(db.list_merge_points()) == 3


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



# ---------------------------------------------------------------------------
# What the promote gate's fallback path costs.
#
# A stack whose offered series contain one mutually exclusive pair cannot be
# promoted whole: every set containing both fails, so the full set can never
# clear the candidate gate and the short-circuit above cannot fire.
#
# The unit of cost is the suite, and these tests count it with a gate that
# appends one byte per invocation. The ladder and the [promotion] block are
# solomon's, shrunk: `compiles`, then a `long` suite that IS the candidate gate
# — `long` is what makes a passing set leave the untested pool between ticks.
# (solomon measured 10-12 x 287 s on a four-series press, 2026-10-26.)

def _offer_exclusive_pair(repo, n):
    """main plus branches b1..bn. b1 and b2 rewrite the same line of base.txt,
    so they are mutually exclusive: any combination holding both conflicts.
    Every branch also adds its own m<i> marker, which is what lets a gate below
    see how many series the set it is being run over holds."""
    for i in range(1, n + 1):
        repo.branch(f"b{i}")
        repo.write(f"m{i}", f"{i}\n")
        if i == 1:
            repo.commit_file("base.txt", "line1\nONE\nline3\n", f"b{i}")
        elif i == 2:
            repo.commit_file("base.txt", "line1\nTWO\nline3\n", f"b{i}")
        else:
            repo.commit_file(f"f{i}.txt", f"{i}\n", f"b{i}")
        repo.git("checkout", "-q", "main")
    return [f"b{i}" for i in range(1, n + 1)]


COUNT_CFG = """
[quilt]
base = "main"
branches = [{branches}]

[[gate]]
name = "compiles"
cmd = "true"

[[gate]]
name = "suite"
cmd = 'printf x >> {counter}; [ $(ls -1 m[0-9] | wc -l) -le {limit} ]'
long = true

[targets]
next = "suite"

[promotion]
target = "next"
candidate_gate = "suite"
final_gate = "promote"
final_cmd = "true"
"""


def _counting_cfg(tmp_path, branches, counter, limit=99):
    p = tmp_path / "counting.toml"
    p.write_text(COUNT_CFG.format(
        branches=", ".join(f'"{b}"' for b in branches), counter=counter,
        limit=limit))
    return gates.load_config(p)


def _conflict_free(n):
    """Non-empty subsets of n branches that avoid the exclusive pair: 2^n - 1
    of them all told, minus the 2^(n-2) that hold both members."""
    return 2 ** n - 1 - 2 ** (n - 2)


def _suites(counter):
    return len(counter.read_text()) if counter.exists() else 0


def _tick_until_idle(repo, db, cfg, counter, n):
    """Tick until `schedulable` says a further tick would do nothing, and
    return the suites the whole run cost. The two have to agree: a tick that
    stops early leaves combinations unprobed, and an idle predicate that does
    not know it would report them as pending forever and tick again."""
    for _ in range(20):
        scheduler.tick(repo.path, db, cfg)
        if scheduler.schedulable(repo.path, db, cfg) == []:
            return _suites(counter)
    pytest.fail(f"the pool never went idle in 20 ticks at n={n}"
                f" after {_suites(counter)} suites")


@pytest.mark.parametrize("n", [3, 4, 5])
def test_the_conflicting_offer_costs_one_suite_in_a_tick(repo, db, tmp_path, n):
    """The largest conflict-free subset of this stack is a size-(N-1) set, and
    the tick has to reach it without running the suite over the subsets below
    it: a suite is the whole cost of a press, and each one that is not the
    answer is spent for nothing."""
    branches = _offer_exclusive_pair(repo, n)
    counter = tmp_path / "suites"
    cfg = _counting_cfg(tmp_path, branches, counter)

    report = scheduler.tick(repo.path, db, cfg)

    assert report["short_circuit"] is False        # the full set cannot be it
    assert _suites(counter) == 1, (
        f"{_suites(counter)} suites in one tick for {n} series with one "
        f"incompatible pair; the power set holds {_conflict_free(n)} "
        f"conflict-free subsets and the answer is one of them")


@pytest.mark.parametrize("n", [3, 4, 5])
def test_the_conflicting_offer_costs_one_suite_until_the_pool_is_idle(
        repo, db, tmp_path, n):
    """The same cost seen from `schedulable` — and the number that matches the
    field: four series, eleven conflict-free subsets, eleven suites, measured
    at 287 s each on 2026-10-26."""
    branches = _offer_exclusive_pair(repo, n)
    counter = tmp_path / "suites"
    cfg = _counting_cfg(tmp_path, branches, counter)

    suites = _tick_until_idle(repo, db, cfg, counter, n)

    assert suites == 1, (
        f"{suites} suites over the whole run for {n} series with one "
        f"incompatible pair; the power set holds {_conflict_free(n)} "
        f"conflict-free subsets")


@pytest.mark.parametrize("n,limit", [(4, 2), (5, 3)])
def test_the_descent_goes_on_past_a_set_that_fails_the_gate(
        repo, db, tmp_path, n, limit):
    """The largest conflict-free set is not always the answer: a set can merge
    clean and fail the suite, and the tick has to try the next size down rather
    than settle for a smaller set than the one that would pass.

    The gate here passes only a set of `limit` series or fewer, so both clean
    size-(N-1) sets fail and the first clean set at `limit` is the answer: two
    suites above the answer, one on it."""
    branches = _offer_exclusive_pair(repo, n)
    counter = tmp_path / "suites"
    cfg = _counting_cfg(tmp_path, branches, counter, limit=limit)

    suites = _tick_until_idle(repo, db, cfg, counter, n)

    assert suites == 3, (
        f"{suites} suites for {n} series; the two clean size-{n - 1} sets fail "
        f"the gate and the first clean size-{limit} set passes")
    frozen = candidate.freeze(repo.path, db, cfg)
    assert len(db.get_merge_point(frozen["merge_point"])["member_patch_ids"]) \
        == limit
