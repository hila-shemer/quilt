# next-manager: loom refused an empty gate ladder - what is left

context:
- /home/hila/proj/manager-rightwaygo/quilt-wt/loom-empty-gate (this worktree, branch `fix/loom-empty-gate`)
- /home/hila/cc-cmds/quilt-loom-empty-gate.{sh,check,bg.sh,why,reject}
- /home/hila/cc-cmds/quilt-loom-empty-gate.report.md (the review page's source md)
- alexandria:2026-11-11-quilt-loom-refuses-an-empty-gate-ladder-instead-of-calling-i
- /home/hila/proj/manager-rightwaygo/rightwaygo-wt/slice14-linearize/internal/loomlinearize/loomlinearize.go
- /home/hila/.claude/claude-control/results/20261111-000553-quilt-PerkyViper.md

## State

Two commits on `fix/loom-empty-gate`, off `origin/main` `0cbe9f2`:

- `a360078` `test(quilt): pin that the commit-level solver cannot break a cycle`
- `36ee3e4` `fix(quilt): loom refuses an empty gate ladder instead of calling it green`

Full suite green at the tip: `260 passed` (`nice -n 10 python3 -m pytest tests -q
-p no:cacheprovider -n 14`, 62-84 s on a loaded box). The three new tests are red
on the unfixed tree: the two refusal tests `DID NOT RAISE`, and the claim-2 pin
is new.

The land row is filed and **pending**. Nothing is on `main` until Hila presses it
and the `run_bg` job reports.

## What the change is

`linearize._require_ladder(cfg)` - one refusal, three doors: `solve`,
`_solve_commits`, and `harvest.run`. It raises `gates.ConfigError("quilt.toml:
the [gate] ladder is empty - loom will not publish an unverified series")` before
the first cherry-pick, before the first pool, and before any ref is written.

Export that name to Go as `RequireLadder` if the port wants one spelling for it -
the Go half is described below.

## Left undone, deliberately

1. **Claim 2 is Hila's call, not a repair.** The commit-granular solver
   (`_commit_interleave` / `_solve_commits`) can never answer a cycle: an
   increment-level edge expands to *every* commit of the prerequisite, so
   contracting commits back to increments recovers the increment graph exactly.
   Pinned by `test_commit_interleave_is_never_the_answer_to_a_cycle` (63 edge
   subsets, 39 cycles, 0 answers). Finer commit-level dependency evidence is what
   would change it. Put to her on the page with a recommendation; no code moved.
2. **The same vacuous green one function away, not fixed.**
   `scheduler.schedulable` reads `db.highest_gate(...) == last_rung`, and with an
   empty ladder both sides are None, so every probed merge point reads as having
   cleared the whole ladder and `quilt idle` says idle with `gate_status` empty.
   Measured: 3 merge points, `highest_gate` None on all three, 0 `gate_status`
   rows, `schedulable -> []`. **No consequence today** - `gates.ready_targets`
   guards on `highest is None`, `candidate.clears_candidate_gate` demands
   `highest is not None`, and `candidate.freeze` therefore promotes nothing, so
   quilt's non-loom half is fail-safe end to end. It is a reading for the design
   pass, and it is why the fix is in loom and not in `load_config`.
3. **`harvest._fully_green` is a byte-identical copy of `linearize._fully_green`.**
   Both are now unreachable with an empty ladder, so the duplicate is a
   maintenance smell and not a live defect. Deduping it is a refactor, not this
   fix; harvest already reaches into `linearize._cherry_pick`, so the precedent
   for the import exists.
4. **quilt-live needs a re-point after the land.** It is a detached-HEAD worktree
   of this repo sitting at `0cbe9f2`
   (`/home/hila/proj/manager-rightwaygo/quilt-live`). The Manager files that row;
   this one does not.

## The Go half (rightwaygo `internal/loomlinearize`, slice 14, row #26467)

It was built at canonical `0cbe9f2` and it *reproduces* the vacuous green on
purpose, so it now diverges in the one direction that matters: it publishes where
the Python refuses. What it must change to follow:

- `Solve` (`solve.go`) and the commit-level materializer (`interleave.go`, the
  second `fullyGreen` call site) each need the refusal, placed exactly where the
  Python places it: **before** `quiltgit.UpdateRef(repo, StagingRef, baseSHA)`.
  The package has no error type of its own - `Solve` already returns `error`, so
  one `fmt.Errorf` with the same words is the whole change.
- `fullyGreen`'s `if len(cfg.Gates) == 0 { return !ok }` branch becomes
  unreachable. Delete it (or leave it inert behind a comment) - either way the
  doc block above it ("The empty ladder is green, and that is the Python's
  answer") is now false and must be rewritten.
- The package doc's finding #1 ("An empty ladder lands everything") is resolved
  rather than tolerated, and finding #2 (unreachable commit path) stands.
- **The frozen oracle must be re-recorded**, and this is the part that cannot be
  done by hand: `testdata/gen_oracle.py` re-run against the new canonical
  revision. Two entries change - `fully_green[0]`
  (`{"gates": [], "highest": null, "out": true}`, the case the doc calls out as
  the first of the two carrying the module's contract) and the step
  `solve_empty_ladder` (step 4, the vacuous publish). `main_sha` moves with them.
  The revision pin is the oracle's own `main_sha` field, not a quilt sha.
- `TestEmptyLadderStillLandsEverything` inverts into a refusal test, and
  `TestOracleFullyGreen`'s comment about "the first and the sixth-from-last"
  needs its count fixed.
- The vendored `src/quilt/loom/linearize.py` in rightwaygo must be re-vendored to
  canonical: the live differential re-runs the generator against `src/quilt`, so
  a stale vendored copy would make the differential disagree with the oracle it
  just re-recorded.

Do not do any of this by editing the oracle's JSON - the generator is the source
and the oracle is its output.

## Land row notes

The check pins the tip **by descent** (`merge-base --is-ancestor`), the code by
the **tree** of `src/quilt` and of `tests`, and the change's **surface** (four
paths, by name). It pins no main sha: the `run_bg` rebases at press time, so the
landed sha is not the filed tip whenever main moves - verify by content on `main`,
never on the sha.

The `run_bg` gate is quilt's own pytest suite (canonical quilt has no `ci.sh`,
no Makefile and no `.github`): `TMPDIR=/home/hila/tmp/quilt-pytest`,
`PYTHONPATH=src`, `nice -n 10`, `-n 13` - a quarter of the box, because several
Managers test at once, and the same thread count the three quilt lands before
this one used. Measured on this tree: **62-103 s** depending on how loaded the
box is, 260 passed either way.
