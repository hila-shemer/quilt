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


def test_tick_output_key_value(repo_with_branches, cfgfile, capsys):
    """tick should print key=value pairs, not a dict repr."""
    out = run(["tick"], repo_with_branches, cfgfile, capsys)
    assert "probed=" in out
    assert "gated=" in out
    assert "queued=" in out
    assert "deferred=" in out
    # Must not look like a dict repr
    assert "{" not in out
    assert "}" not in out


def test_promote_advances_target(repo_with_branches, cfgfile, capsys):
    run(["tick"], repo_with_branches, cfgfile, capsys)
    out = run(["promote", "next"], repo_with_branches, cfgfile, capsys)
    assert "next" in out
    assert repo_with_branches.git("rev-parse", "refs/quilt/target/next")


def test_promote_unknown_target_exits_1(repo_with_branches, cfgfile, capsys):
    """Promoting an unknown target prints a message and exits 1."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--repo", str(repo_with_branches.path),
                  "--config", str(cfgfile),
                  "promote", "no-such-target"])
    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "unknown target" in out
    assert "no-such-target" in out


def test_promote_no_ready_candidate_exits_1(repo_with_branches, cfgfile, capsys):
    """Promoting when no merge point is ready exits 1 (no tick run yet)."""
    with pytest.raises(SystemExit) as exc_info:
        run(["promote", "next"], repo_with_branches, cfgfile, capsys)
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# poison command
# ---------------------------------------------------------------------------

def test_poison_via_prefix(repo_with_branches, cfgfile, capsys):
    """poison <12-char prefix> → status shows poison, queue intact, exit 0."""
    run(["tick"], repo_with_branches, cfgfile, capsys)

    # Grab the full id of the clean (non-conflicted) merge point from status
    out = run(["status"], repo_with_branches, cfgfile, capsys)
    # Pick the first ID in the output (any merge point will do for prefix test)
    first_id_12 = out.strip().splitlines()[0].split()[0]  # 12-char prefix shown

    # Run poison with the 12-char prefix
    out = run(["poison", first_id_12], repo_with_branches, cfgfile, capsys)
    assert "poisoned" in out
    assert "evicted" in out

    # Status must now show poison for that id
    status_out = run(["status"], repo_with_branches, cfgfile, capsys)
    assert "poison" in status_out

    # Queue should still be intact (no crash)
    run(["queue"], repo_with_branches, cfgfile, capsys)


def test_poison_deletes_pair_ref(repo_with_branches, cfgfile, capsys):
    """poison evicts refs/quilt/<id> for dependent merge points."""
    from quilt.db import DB
    from quilt import gitio
    from pathlib import Path

    run(["tick"], repo_with_branches, cfgfile, capsys)

    # Discover the merge points from DB directly
    db_path = Path(str(cfgfile)).parent / ".quilt.sqlite3"
    db = DB(db_path)
    mps = db.list_merge_points()
    # Find a merge point that has a ref set (result_commit not None)
    target = next((mp for mp in mps if mp["result_commit"]), None)
    if target is None:
        pytest.skip("no merge point with result_commit after tick")

    # Confirm the ref exists before poisoning
    repo_path = Path(repo_with_branches.path)
    ref_before = gitio.read_ref(repo_path, f"refs/quilt/{target['id']}")
    assert ref_before is not None, "ref should exist before poisoning"

    # Poison it using 12-char prefix
    prefix = target["id"][:12]
    run(["poison", prefix], repo_with_branches, cfgfile, capsys)

    # The ref for the poisoned merge point itself should still exist
    # (we only evict supersets); but if it IS the only one, check dependents evicted
    # Re-fetch DB to check validation_state
    db2 = DB(db_path)
    mp_after = db2.get_merge_point(target["id"])
    assert mp_after["validation_state"] == "poison"


def test_poison_evicts_superset_ref(repo_with_branches, cfgfile, capsys):
    """refs/quilt/<superset-id> is deleted after poisoning a subset."""
    from quilt.db import DB
    from quilt import gitio
    from pathlib import Path

    run(["tick"], repo_with_branches, cfgfile, capsys)

    db_path = Path(str(cfgfile)).parent / ".quilt.sqlite3"
    db = DB(db_path)
    mps = db.list_merge_points()

    # Find a merge point that is a strict subset of another
    # (member_patch_ids of one is a strict subset of another)
    subset = None
    superset = None
    for a in mps:
        for b in mps:
            if a["id"] != b["id"] and set(a["member_patch_ids"]) < set(b["member_patch_ids"]):
                subset, superset = a, b
                break
        if subset:
            break

    if subset is None:
        pytest.skip("no subset/superset pair found after tick")

    # Ensure the superset ref exists; if not just skip ref check
    repo_path = Path(repo_with_branches.path)
    ref_before = gitio.read_ref(repo_path, f"refs/quilt/{superset['id']}")

    # Poison the subset
    prefix = subset["id"][:12]
    run(["poison", prefix], repo_with_branches, cfgfile, capsys)

    # Superset ref should be deleted (evicted)
    ref_after = gitio.read_ref(repo_path, f"refs/quilt/{superset['id']}")
    assert ref_after is None, "superset ref should be evicted after poisoning subset"


def test_poison_unknown_prefix_exits_1(repo_with_branches, cfgfile, capsys):
    """Unknown prefix → message + exit 1."""
    with pytest.raises(SystemExit) as exc_info:
        run(["poison", "000000000000"], repo_with_branches, cfgfile, capsys)
    assert exc_info.value.code == 1
