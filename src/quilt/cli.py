"""quilt CLI."""
import argparse
import sys
from pathlib import Path

from . import gates as gates_mod
from . import agent, gitio, llm, probe, resolve, scheduler, triage
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
    pp = sub.add_parser("poison")
    pp.add_argument("merge_point_id")
    sub.add_parser("triage")
    sub.add_parser("resolve")
    sub.add_parser("diagnose")
    args = ap.parse_args(argv)

    repo = Path(args.repo)
    cfg = gates_mod.load_config(Path(args.config))
    db = DB(Path(args.db) if args.db else Path(args.config).parent / ".quilt.sqlite3")

    if args.cmd == "probe":
        for r in probe.probe_all(repo, cfg.base, cfg.branches, db):
            print(f"{r['id'][:12]} {r['construction']:9} {'+'.join(r['branches'])}")
    elif args.cmd == "tick":
        r = scheduler.tick(repo, db, cfg)
        print(" ".join(f"{k}={v}" for k, v in r.items()))
    elif args.cmd == "status":
        for mp in db.list_merge_points(gitio.tree_of(repo, cfg.base)):
            highest = db.highest_gate(mp["id"], mp["base_commit_sha"], cfg.ladder)
            print(f"{mp['id'][:12]} {mp['construction']:9} "
                  f"{mp['validation_state']:9} gate={highest or '-'}")
    elif args.cmd == "queue":
        for w in db.pending_work():
            print(f"{w['id']:4} {w['kind']:10} {w['target_id'][:12]} {w['detail'][:60]}")
    elif args.cmd == "promote":
        if args.target not in cfg.targets:
            print(f"unknown target: {args.target}")
            sys.exit(1)
        required = cfg.targets[args.target]
        candidates = [mp for mp in db.list_merge_points(gitio.tree_of(repo, cfg.base))
                      if args.target in gates_mod.ready_targets(db, cfg, mp["id"])
                      and mp["result_commit"]]
        candidates.sort(key=lambda mp: len(mp["member_patch_ids"]), reverse=True)
        if not candidates:
            print(f"no merge-point ready for {args.target}")
            sys.exit(1)
        best = candidates[0]
        gitio.update_ref(repo, f"refs/quilt/target/{args.target}", best["result_commit"])
        print(f"{args.target} -> {best['result_commit'][:12]} "
              f"(gate {required}, {len(best['member_patch_ids'])} members)")
    elif args.cmd == "triage":
        try:
            r = triage.drain(db, cfg)
        except llm.LLMError as e:
            print(e)
            sys.exit(1)
        print(" ".join(f"{k}={v}" for k, v in r.items()))
        if r["errors"]:
            sys.exit(1)
    elif args.cmd == "resolve":
        items = db.work_by_state("triaged", kind="conflict")
        done = 0
        try:
            for item in items:
                if agent.resolve_conflict(repo, db, cfg, item):
                    done += 1
        except llm.LLMError as e:
            print(e)
            sys.exit(1)
        print(f"resolved={done} remaining={len(items) - done}")
    elif args.cmd == "diagnose":
        items = db.work_by_state("triaged", kind="test_fail")
        done = 0
        try:
            for item in items:
                v = agent.diagnose_failure(repo, db, cfg, item)
                if v:
                    done += 1
                    print(f"{item['target_id'][:12]} -> {v['attribution']}"
                          f" ({v.get('reason', '')})")
        except llm.LLMError as e:
            print(e)
            sys.exit(1)
        print(f"diagnosed={done} remaining={len(items) - done}")
    elif args.cmd == "poison":
        prefix = args.merge_point_id
        matches = db.find_merge_point(prefix)
        if len(matches) == 0:
            print(f"no merge-point matches prefix: {prefix}")
            sys.exit(1)
        if len(matches) > 1:
            print(f"ambiguous prefix {prefix!r} matches {len(matches)} merge-points: "
                  + ", ".join(m[:12] for m in matches))
            sys.exit(1)
        full_id = matches[0]
        cascade_ids = resolve.poison_merge_point(repo, db, full_id)
        print(f"poisoned {full_id}, evicted {len(cascade_ids)} dependents")


if __name__ == "__main__":
    main()
