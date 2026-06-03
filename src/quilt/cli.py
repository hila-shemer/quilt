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
        candidates = [mp for mp in db.list_merge_points(gitio.tree_of(repo, cfg.base))
                      if args.target in gates_mod.ready_targets(db, cfg, mp["id"])
                      and mp["result_commit"]]
        candidates.sort(key=lambda mp: len(mp["member_patch_ids"]), reverse=True)
        if not candidates:
            print(f"no merge-point ready for {args.target}")
            return
        best = candidates[0]
        gitio.update_ref(repo, f"refs/quilt/target/{args.target}", best["result_commit"])
        print(f"{args.target} -> {best['result_commit'][:12]} "
              f"(gate {required}, {len(best['member_patch_ids'])} members)")


if __name__ == "__main__":
    main()
