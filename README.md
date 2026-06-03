# Quilt

Quilt is a standalone integration pipeline that probes all combinations (N ≤ 5)
of feature branches against a base via `git merge-tree`, runs a monotone gate
ladder against each clean merge-point, caches results in SQLite, and promotes
passing candidates to named target refs.

Inspired by the kernel subsystem-tree → linux-next → mainline flow.  LLM agents
are **off the happy path**: conflicts and test failures land in a work queue, but
a merge-point that merges cleanly and clears the gate ladder reaches promotion
with zero agent calls.

## How it works

1. **Enumerate combinations.** For up to 5 feature branches Quilt tries every
   subset ({branch}, {A,B}, {A,B,C}, …).  Each combination is a *merge-point*.

2. **Probe with `merge-tree`.** Sequential pairwise merges without touching the
   working tree.  A single conflict anywhere marks the whole combination
   `conflict`; otherwise it is `clean` and a result commit is stored under
   `refs/quilt/<id>`.

3. **Content-keyed identity.**
   ```
   id = hash(base_tree_sha, sorted(member_patch_ids))
   ```
   The key is based on the **tree** of the base (not the commit SHA) and on
   `git patch-id --stable` per branch.  Base commits that don't change content
   keep all merge-points cache-hot; rebases and metadata edits don't invalidate.

4. **Gate ladder.** Each gate is a shell command.  Gates run in declaration
   order inside a temporary worktree of the merge result.  Results are cached
   per `(merge-point, gate, base-commit)`; a row's absence means stale.  The
   first failing gate breaks the ladder and enqueues a `test_fail` work item.

5. **K-bounded heavy-test scheduling.** `quilt tick` advances one unit of work
   (probe → gates → …) each call.  A scheduler caps concurrent heavy tests.

6. **Promotion.** `quilt promote <target>` fast-forwards `refs/quilt/target/<target>`
   to the best candidate (highest member count) that has passed all required gates.

## Quickstart

```bash
pip install -e .
```

Write a `quilt.toml` in your repository root:

```toml
[quilt]
base     = "main"
branches = ["feat/foo", "feat/bar", "feat/baz"]

[[gate]]
name = "compiles"
cmd  = "make -C {workdir} -j$(nproc)"

[[gate]]
name = "unit-tests"
cmd  = "make -C {workdir} test"

[targets]
next = "compiles"
main = "unit-tests"
```

`{workdir}` in `cmd` is replaced by the absolute path of the temporary worktree
where the merged result is checked out.

### Commands

| Command | Description |
|---|---|
| `quilt tick` | Advance the pipeline one step (probe + gate ladder). |
| `quilt status` | Show each merge-point and its highest passing gate. |
| `quilt queue` | Show pending work items (conflicts, test failures). |
| `quilt promote <target>` | Promote the best ready candidate to `refs/quilt/target/<target>`. |

All commands accept `--repo <path>` (default: `.`) and `--config <path>`
(default: `quilt.toml`).

### Gate ladder notes

- Order = declaration order in the TOML file.
- Results are cached per base commit; staleness = absence of a row for the
  current base commit SHA.
- Moving the base forward (e.g. after merging a previous round) re-runs only
  gates whose results are missing for the new base; it does not clear passing
  results from older bases.

## Reference

- **Design:** [`mds/quilt-design.md`](mds/quilt-design.md)
- **Implementation plan:** [`docs/plans/`](docs/plans/)
