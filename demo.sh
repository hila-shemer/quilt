#!/usr/bin/env bash
# Narrated CLI demo of quilt. Runs real commands in a fresh terminal.
#
#   ./demo.sh              run from this source tree in place (default)
#   DEMO_PAUSE=0 ./demo.sh no read-pauses (fast replay / self-test)
#
# Stable-only: quilt is a solo extraction with no --staging/--next channels.
# Note on versions: a `quilt` may be on PATH, but it resolves to a DIFFERENT
# checkout (the rightwayc repo it was first installed from). To honestly demo
# *this* repo we run its own source via `python -m quilt.cli` -- stale is fine.
#
# Deliberately NO 'set -e': a demo command may exit non-zero on purpose and we
# want the real exit shown on screen, not the script aborted. Fresh shell ->
# absolute paths only.
set -uo pipefail

SRC=/home/hila/proj/quilt               # source dir; assumed not to move (stale ok)
PAUSE=${DEMO_PAUSE:-3}

case ${1:-} in
  '') ;;
  *) echo "usage: $0   (stable-only; run with DEMO_PAUSE=0 for fast replay)" >&2; exit 2 ;;
esac

# Run quilt from THIS repo's source, not whatever 'quilt' is on PATH (that one
# points at a different checkout). No build step -- it's pure Python 3.12+.
QUILT() { PYTHONPATH="$SRC/src" python3 -m quilt.cli "$@"; }
TOOL="python3 -m quilt.cli  (from $SRC/src)"

say() { printf '# %s\n' "$*"; sleep "$(( PAUSE > 0 ? 1 : 0 ))"; }     # explanation line (tight: no leading blank)
run() { printf '$ %s\n' "$*"; eval "$*"; sleep "$PAUSE"; }            # show command directly under its narration, run, pause
sec() { printf '\n'; }                                                # one blank line at a section boundary

# --- overview: assume the viewer last saw this months ago, name alone won't do
say "quilt -- a branch-combination integration pipeline (a tiny linux-next)."
say "It probes EVERY subset of up to 5 feature branches against a base, merges each"
say "with git merge-tree (no working-tree churn), runs a gate ladder, and promotes the"
say "LARGEST combo that passes -- zero LLM calls on the happy path. Conflicts/test-fails"
say "fall off into a work queue you drain. Demoing: $TOOL"

# --- proof of life: cheap facts that work even if nothing is set up
sec
run "QUILT --help 2>&1 | head -4"
sec
run "echo subcommands: ; QUILT --help 2>&1 | tr ',' '\n' | grep -oE '(probe|tick|status|queue|promote|poison|triage|resolve|diagnose|freeze|advance|backprop)' | sort -u | paste -sd' '"

# --- the headline: a real integration round in a throwaway git repo
sec
say "Now watch it actually integrate two feature branches into a base, for real."
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
export GIT_AUTHOR_NAME=demo GIT_AUTHOR_EMAIL=demo@x
export GIT_COMMITTER_NAME=demo GIT_COMMITTER_EMAIL=demo@x

# base + two independent branches that edit DIFFERENT lines -> they merge clean.
( cd "$tmp" && git init -q -b main \
  && printf 'line1\nline2\nline3\n' > app.txt && git add app.txt && git commit -qm base \
  && git checkout -q -b feat-a && printf 'line1-A\nline2\nline3\n'  > app.txt && git commit -qam 'feat A: edit line1' \
  && git checkout -q main && git checkout -q -b feat-b && printf 'line1\nline2\nline3-B\n' > app.txt && git commit -qam 'feat B: edit line3' \
  && git checkout -q main )

cat > "$tmp/quilt.toml" <<'TOML'
[quilt]
base     = "main"
branches = ["feat-a", "feat-b"]

[[gate]]
name = "smoke"
cmd  = "grep -q line2 {workdir}/app.txt"   # trivial gate: the merged tree still has line2

[targets]
main = "smoke"
TOML
sec
say "Two branches off 'main' (each edits a different line) + a one-gate quilt.toml:"
run "cat $tmp/quilt.toml"

sec
say "probe enumerates every subset and merge-trees it; clean ones become merge-points:"
run "QUILT --repo $tmp --config $tmp/quilt.toml probe"

sec
say "tick re-probes + runs the gate ladder on each clean merge-point in one pass:"
run "QUILT --repo $tmp --config $tmp/quilt.toml tick"

sec
say "status: three merge-points (each single, plus the pair) all cleared gate 'smoke':"
run "QUILT --repo $tmp --config $tmp/quilt.toml status"

sec
say "promote fast-forwards refs/quilt/target/main to the LARGEST passing combo (the pair):"
run "QUILT --repo $tmp --config $tmp/quilt.toml promote main && git -C $tmp log --oneline -1 refs/quilt/target/main"

sec
say "That's quilt: clean merges + a passing gate ladder => an integrated ref, no agent. Source: $SRC"
