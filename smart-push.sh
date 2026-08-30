#!/bin/bash
# smart-push.sh - mirror this repo's local refs up to its remote(s), force-pushing
# only where the remote content is provably identical to ours, and HALTING the
# instant a remote ref holds work we don't have locally.
#
# A push is only safe when you aren't clobbering anything, and there are two ways
# to be sure. Either the remote tip is an ancestor of ours - a plain fast-forward,
# no force needed - or it has diverged by hash but carries no *change* we don't
# already have, which is what a rebase (optionally with new commits stacked on top)
# looks like. That second test is patch-id containment, NOT a tip-vs-tip diff: a
# local commit added on top must not read as "the remote has work we'd lose". When
# the remote does hold a commit whose patch isn't in our history, or its object
# can't be resolved at all, we stop - a human looks before we overwrite.
#
# Per ref, after `git remote update`: missing on remote -> push (nothing to clobber),
# equal hash -> skip, remote behind us -> fast-forward push, our rebased history
# (no remote-only change) -> force-push, anything we'd overwrite -> halt. Covers
# refs/heads, refs/quilt and tags. Acts on $PWD.
#
#   smart-push.sh            classify, print the plan, then read-gate before pushing
#   smart-push.sh -y         skip the gate (for looping over many repos)
#   smart-push.sh -n         dry-run: classify and print, never push, never prompt
#   smart-push.sh [remote..] restrict to named remotes (default: every `git remote`)
set -uo pipefail

ASSUME_YES=0
DRY_RUN=0
REMOTES=()
for arg in "$@"; do
    case "$arg" in
        -y|--yes)     ASSUME_YES=1 ;;
        -n|--dry-run) DRY_RUN=1 ;;
        -h|--help)    sed -n '2,14p' "$0"; exit 0 ;;
        -*)           echo "smart-push: unknown flag $arg" >&2; exit 2 ;;
        *)            REMOTES+=("$arg") ;;
    esac
done

git rev-parse --git-dir >/dev/null 2>&1 || { echo "smart-push: not a git repo ($PWD)" >&2; exit 2; }

# Default to every configured remote - "iterate all remote repos".
if ((${#REMOTES[@]} == 0)); then
    mapfile -t REMOTES < <(git remote)
fi
((${#REMOTES[@]})) || { echo "smart-push: no remotes configured, nothing to do"; exit 0; }

# One fetch refreshes tracking refs and pulls the objects we need to diff against.
# ls-remote (below) is what we actually trust for the comparison, but without the
# objects local the content check can't run.
echo "smart-push: git remote update --prune"
git remote update --prune || { echo "smart-push: remote update failed" >&2; exit 1; }

# Namespaces we mirror. Tags and quilt refs share a namespace with the remote
# (no refs/remotes/* shadow), which is why ls-remote is the only honest source.
REF_GLOBS=(refs/heads refs/quilt refs/tags)

pushed_anything=0

for remote in "${REMOTES[@]}"; do
    echo
    echo "=== $remote ==="

    # Authoritative remote state: refname -> sha. Peeled tag lines (refs/tags/x^{})
    # would overwrite the tag-object sha with the commit sha, so drop them.
    declare -A RSHA=()
    while IFS=$'\t' read -r sha ref; do
        [[ "$ref" == *'^{}' ]] && continue
        RSHA["$ref"]="$sha"
    done < <(git ls-remote "$remote") || { echo "smart-push: ls-remote $remote failed" >&2; exit 1; }

    new_refs=()    # absent on remote          -> plain push
    ff_refs=()     # remote behind us          -> fast-forward push
    force_refs=()  # our rebased history       -> force push
    diff_refs=()   # real divergence / unverifiable -> halt
    equal=0

    while read -r refname local_sha; do
        if [[ -z "${RSHA[$refname]+set}" ]]; then
            new_refs+=("$refname")
        elif [[ "${RSHA[$refname]}" == "$local_sha" ]]; then
            ((equal++))
        else
            rsha="${RSHA[$refname]}"
            # Can't resolve the remote object to a commit (e.g. a refs/quilt/* the
            # fetch never brought down)? Then we can't reason about it, so halt -
            # never overwrite something we can't read.
            if ! git rev-parse -q --verify "${rsha}^{commit}" >/dev/null 2>&1; then
                diff_refs+=("$refname")
            elif git merge-base --is-ancestor "$rsha" "$local_sha"; then
                ff_refs+=("$refname")     # remote strictly behind - fast-forward
            else
                # Diverged by hash. Force is safe only if the remote holds no change
                # we don't already carry - our history rebased, maybe with commits
                # stacked on top. --cherry-pick drops remote commits whose patch has
                # a twin in local; --right-only --count is what's left = what a force
                # would actually lose. Empty string (rev-list error) is treated as
                # unsafe, not zero.
                lose=$(git rev-list --right-only --cherry-pick --no-merges --count \
                       "$local_sha...$rsha" 2>/dev/null) || lose=
                if [[ "$lose" == 0 ]]; then
                    force_refs+=("$refname")
                else
                    diff_refs+=("$refname")
                fi
            fi
        fi
    done < <(git for-each-ref --format='%(refname) %(objectname)' "${REF_GLOBS[@]}")

    # Any divergence stops everything before we touch the remote - no partial push.
    if ((${#diff_refs[@]})); then
        echo "HALT: $remote carries commits a force would lose on these refs:"
        for r in "${diff_refs[@]}"; do
            rsha="${RSHA[$r]}"
            echo "  $r"
            if git rev-parse -q --verify "${rsha}^{commit}" >/dev/null 2>&1; then
                git --no-pager log --right-only --cherry-pick --no-merges \
                    --format='    lose: %h %s' "$(git rev-parse "$r")...$rsha"
            else
                echo "    (remote object not present locally - cannot inspect)"
            fi
        done
        echo "Resolve by hand, then re-run."
        exit 1
    fi

    printf '  equal (skip): %d\n' "$equal"
    ((${#new_refs[@]}))   && printf '  push  (new):  %s\n' "${new_refs[*]}"
    ((${#ff_refs[@]}))    && printf '  push  (ff):   %s\n' "${ff_refs[*]}"
    ((${#force_refs[@]})) && printf '  force:        %s\n' "${force_refs[*]}"

    if ((${#new_refs[@]} == 0 && ${#ff_refs[@]} == 0 && ${#force_refs[@]} == 0)); then
        echo "  up to date."
        continue
    fi

    if ((DRY_RUN)); then
        echo "  (dry-run, not pushing)"
        continue
    fi

    # The read-gate. --yes skips it; a non-interactive run with neither flag falls
    # through to "show plan, push nothing" so a loop can't force-push unattended.
    if ((! ASSUME_YES)); then
        if [[ -t 0 ]]; then
            read -r -p "  push to $remote? [enter to push / ctrl-c to abort] " _
        else
            echo "  (non-interactive: re-run with -y to push)"
            continue
        fi
    fi

    plain=("${new_refs[@]}" "${ff_refs[@]}")
    if ((${#plain[@]})); then
        specs=(); for r in "${plain[@]}"; do specs+=("$r:$r"); done
        git push "$remote" "${specs[@]}"
    fi
    if ((${#force_refs[@]})); then
        specs=(); for r in "${force_refs[@]}"; do specs+=("$r:$r"); done
        git push --force "$remote" "${specs[@]}"
    fi
    pushed_anything=1
done

((pushed_anything)) && echo || true
exit 0
