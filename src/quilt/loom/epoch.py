"""Reflow epoch (Loom spec §10) — thrash guard under continuous force-push.

The solved plan is frozen for an epoch. Agents complete rebases against the
frozen plan; their results are accepted only if the epoch has not rolled. The
epoch is a monotone counter stamped on each solve; it rolls when the increment
SET changes (ids or patch identity) — reordering within the same set does not
roll it.
"""
import hashlib

_EPOCH_KEY = "reflow_epoch"
_SETHASH_KEY = "reflow_set_hash"


def _get(db, key: str) -> str | None:
    row = db.conn.execute("SELECT value FROM loom_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _set(db, key: str, value) -> None:
    db.conn.execute(
        "INSERT INTO loom_meta (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)))
    db.conn.commit()


def current(db) -> int:
    v = _get(db, _EPOCH_KEY)
    return int(v) if v is not None else 0


def set_hash(incs) -> str:
    items = sorted(f"{i.id}={i.patches.get('self', '')}|{i.patch_id}" for i in incs)
    return hashlib.sha256("\n".join(items).encode()).hexdigest()


def mint(db, incs) -> int:
    """Stamp the epoch for the current set. Rolls (increments) only when the set
    changed since the last mint; otherwise returns the current epoch unchanged."""
    h = set_hash(incs)
    prev = _get(db, _SETHASH_KEY)
    cur = current(db)
    if prev is None or prev != h:
        cur += 1
        _set(db, _EPOCH_KEY, cur)
        _set(db, _SETHASH_KEY, h)
    return cur


def accept(db, epoch) -> bool:
    """True iff *epoch* is still the current epoch (the plan has not reflowed)."""
    return int(epoch) == current(db)


def roll(db) -> int:
    """Force an epoch boundary for a reason orthogonal to the increment set —
    e.g. a regression-lock harvest changed the base tree (§6.3), invalidating the
    combination cache. Bumps the counter once and stales the set-hash baseline so
    the next `mint` re-rolls (the next plan is genuinely new on the new base).
    Callers batch base-changing lands behind a single `roll`, never dribble."""
    cur = current(db) + 1
    _set(db, _EPOCH_KEY, cur)
    db.conn.execute("DELETE FROM loom_meta WHERE key=?", (_SETHASH_KEY,))
    db.conn.commit()
    return cur
