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
| `quilt status` | Show each merge-point — member branches, highest passing gate, failing gate — and the idle predicate. |
| `quilt idle` | Exit 0 iff nothing will change without new tips or drained items. Scriptable wait condition. |
| `quilt queue [--lines N] [--full] [--state S\|all]` | Pending work items: member branches, gate, exit code, and a failure-first excerpt of the log. |
| `quilt show <work-id>` | The complete stored detail for one work item. |
| `quilt dismiss <work-id> --reason <text>` | Retire an item whose failure was the gate environment's fault. No poison, no attribution. |
| `quilt requeue <work-id>` | Return an item to the queue (undo a triage, a deferral, or a dismissal). |
| `quilt promote <target>` | Promote the best ready candidate to `refs/quilt/target/<target>`. |
| `quilt poison <prefix>` | Mark a merge-point poisoned and cascade-evict all supersets. |
| `quilt triage` | Classify queued work via the cheap model; route trivial/moderate → agent, complex → deferred. |
| `quilt resolve` | Run the capable agent on triaged conflicts; pins `refs/quilt/<id>` (`agent`/`frankenmerge`). |
| `quilt diagnose` | Attribute triaged test failures to a resolution (→ poison + cascade) or a member branch. |
| `quilt freeze` | Freeze the best candidate that cleared `[promotion].candidate_gate` to `refs/quilt/candidate/<target>`. |
| `quilt advance` | Run `[promotion].final_cmd` on the frozen candidate; on pass advance `refs/quilt/target/<target>`. |
| `quilt backprop [--out DIR]` | Export pending frankenmerge glue as patches; detect adoption by patch-id. |

All commands accept `--repo <path>` (default: `.`) and `--config <path>`
(default: `quilt.toml`).

### Reading a failure

`quilt queue` is the whole path — `.quilt.sqlite3` is state, not an interface.
Each item prints which branches it is about, which gate failed with what exit
code, and an excerpt chosen failure-first: lines matching FAIL/ERROR/assert win
the budget, `ok:` lines never do, and omissions are marked so you know it is an
excerpt. `quilt show <id>` prints the log entire.

A gate's stdout and stderr share one pipe, so the stored detail is in the
child's own write order. (That means a build system's parting "Build completed
successfully" can still land last on a failing gate — it really was written
last; the excerpt shows you the failure regardless.)

When the failure is the gate's own fault — a missing dependency path, a
misconfigured runner — `quilt dismiss <id> --reason ...` retires it without a
verdict. The gate result stays `fail`, so the next tick re-runs it and the item
comes back if the environment is still broken.

### Gate ladder notes

- Order = declaration order in the TOML file.
- Results are cached per base commit; staleness = absence of a row for the
  current base commit SHA.
- Moving the base forward (e.g. after merging a previous round) re-runs only
  gates whose results are missing for the new base; it does not clear passing
  results from older bases.

### LLM agents (off the happy path)

Agents are shell commands configured in `quilt.toml` — the prompt arrives on
stdin; triage/diagnose answer with a JSON object on stdout, resolve edits the
conflicted worktree it is started in:

```toml
[llm]
triage_cmd   = "claude -p --model claude-haiku-4-5"
resolve_cmd  = "claude -p --permission-mode acceptEdits"
diagnose_cmd = "claude -p"
```

The agent loop: `quilt tick` (deterministic; fills the queue) → `quilt triage`
(cheap model routes) → `quilt resolve` / `quilt diagnose` (capable model) →
`quilt tick` again (gates the new resolutions).

Mark a long-running gate with `long = true` to track
`untested → inflight → validated` on merge-points that pass it.

### Promotion loop

```toml
[promotion]
target = "main"
candidate_gate = "t4h"    # ladder rung required to freeze a candidate
final_gate = "t4day"      # recorded gate name for the stress run
final_cmd = "make stress -C {workdir}"
```

`quilt freeze` pins the largest qualifying merge-point to a frozen candidate
ref; `quilt advance` stress-tests exactly that frozen commit and only then
moves `refs/quilt/target/main`. Agent-authored glue commits (frankenmerges)
are tracked in `frankenmerge_fix` and driven `pending → offered → adopted` by
`quilt backprop`.

## Reference

- **Design:** [`mds/quilt-design.md`](mds/quilt-design.md)
- **Implementation plan:** [`docs/plans/`](docs/plans/)
