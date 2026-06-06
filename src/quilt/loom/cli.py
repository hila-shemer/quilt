"""Loom CLI. Subcommands are added per phase.

P3 wires `promote` (FF + milestone stress) and `propose-push` (the hard safety
gate). Global flags mirror quilt: --repo, --config, --db.
"""
import argparse
from pathlib import Path

from .. import gates as gates_mod
from ..db import DB
from . import promote as promote_mod
from . import pushgate, schema


def _open(args):
    repo = Path(args.repo)
    cfg = gates_mod.load_config(Path(args.config))
    dbpath = Path(args.db) if args.db else Path(args.config).parent / ".quilt.sqlite3"
    db = DB(dbpath)
    schema.apply(db.conn)
    return repo, cfg, db


def main(argv=None):
    ap = argparse.ArgumentParser(prog="loom")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--config", default="quilt.toml")
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("promote", help="FF next_staging to a stress-validated milestone")
    pp = sub.add_parser("propose-push", help="surface a push proposal (never pushes)")
    pp.add_argument("remote")
    pp.add_argument("ref")
    pp.add_argument("--outdir", default=None, help="emit patch artifacts here on block")

    args = ap.parse_args(argv)
    repo, cfg, db = _open(args)

    if args.cmd == "promote":
        _promote(repo, cfg, db)
    elif args.cmd == "propose-push":
        _propose_push(repo, cfg, args)


def _promote(repo, cfg, db) -> None:
    res = promote_mod.run(repo, db, cfg)
    if res is None:
        print("promote: nothing above the floor to promote")
    elif res["promoted"]:
        print(f"promote: next_staging advanced to {res['milestone'][:12]}")
    else:
        print(f"promote: held floor — stress failed on {res['milestone'][:12]} "
              "(test_fail enqueued)")


def _propose_push(repo, cfg, args) -> None:
    outdir = Path(args.outdir) if args.outdir else None
    try:
        p = pushgate.propose(repo, cfg, url=args.remote, ref=args.ref, outdir=outdir)
    except pushgate.NotPushable as e:
        print(f"propose-push: REFUSED — {e}")
        return
    except pushgate.PushBlocked as e:
        print(f"propose-push: BLOCKED — {e}\n"
              "(re-run with --outdir to emit patch artifacts)")
        return
    if p.blocked:
        print(f"propose-push: BLOCKED — {p.reason}")
        for a in p.artifacts:
            print(f"  patch: {a}")
        return
    print("propose-push PROPOSAL (nothing pushed — operator approval required):")
    print(f"  url:    {p.url}")
    print(f"  ref:    {p.ref}")
    print(f"  branch: {p.branch}")
    print(f"  commit: {p.commit}")
    if p.diffstat:
        print(p.diffstat)


if __name__ == "__main__":
    main()
