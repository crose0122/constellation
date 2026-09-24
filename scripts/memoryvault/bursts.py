"""Burst culling (V2 CP3, spec B4).

A phone burst is 10-30 near-identical frames taken within a second or two.
Showing all of them buries the one good shot, so at ingest time we:

  1. group photos taken within BURST_WINDOW_S of each other (EXIF time) whose
     pHash is within BURST_PHASH_MAX — close in time AND in picture;
  2. keep the sharpest (variance of the Laplacian: blur flattens edges);
  3. PARK the rest: status='parked', parked_by=<keeper id>. The row, the
     original file, the thumbnail — all kept. Parked photos are simply not
     'screened/tagged/noted', so no stream, search or wall selects them.

Nothing is ever deleted here. `release(photo_id)` puts a parked photo back,
permanently: a released photo is never re-parked by a later cull.
Photos without a capture time are never treated as a burst (two different
days of the same view must not collapse).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image, ImageFilter

from . import config
from .db import finish_run, start_run


def sharpness(path: str | Path) -> float:
    """Variance of a Laplacian over a 1024px greyscale copy. Higher = sharper.
    Resizing first keeps it comparable across resolutions and fast."""
    with Image.open(path) as im:
        g = im.convert("L")
        g.thumbnail((1024, 1024))
        lap = g.filter(ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), scale=1, offset=128))
        a = np.asarray(lap, dtype=np.float32)
    return float(a.var())


def _t(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None


def find_bursts(rows) -> list[list]:
    """rows: dicts with id, taken_at, phash. Returns groups (len >= 2) of rows.
    Chains by time (each frame within the window of the previous one) and
    requires every member to look like the group's first frame."""
    timed = sorted((r for r in rows if _t(r["taken_at"]) is not None and r["phash"]),
                   key=lambda r: (_t(r["taken_at"]), r["id"]))
    groups, cur = [], []
    for r in timed:
        if cur:
            gap = _t(r["taken_at"]) - _t(cur[-1]["taken_at"])
            similar = (imagehash.hex_to_hash(r["phash"])
                       - imagehash.hex_to_hash(cur[0]["phash"])) <= config.BURST_PHASH_MAX
            if gap <= config.BURST_WINDOW_S and similar:
                cur.append(r)
                continue
            if len(cur) > 1:
                groups.append(cur)
        cur = [r]
    if len(cur) > 1:
        groups.append(cur)
    return groups


def cull(conn, sharpness_fn=sharpness) -> dict:
    """Park all but the sharpest frame of every burst among photos that are
    still in the stream. Idempotent: parked photos aren't reconsidered, and a
    keeper stays the keeper."""
    run = start_run(conn, "bursts")
    rows = conn.execute(
        "SELECT id, taken_at, phash, library_path, sharpness FROM photos "
        "WHERE media_kind = 'photo' AND parked_by IS NULL AND burst_released = 0 "
        "AND status IN ('staged','screened','tagged','noted')"
    ).fetchall()
    stats = {"bursts": 0, "parked": 0, "kept": 0}
    for group in find_bursts([dict(r) for r in rows]):
        scored = []
        for r in group:
            s = r["sharpness"]
            if s is None:
                try:
                    s = sharpness_fn(str(config.LIBRARY_ROOT / r["library_path"]))
                except Exception:
                    s = -1.0          # unreadable frame never wins
                conn.execute("UPDATE photos SET sharpness = ? WHERE id = ?", (s, r["id"]))
            scored.append((s, -r["id"], r))   # ties: the earliest id wins, stably
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        keeper = scored[0][2]
        for _, _, r in scored[1:]:
            conn.execute(
                "UPDATE photos SET status = 'parked', parked_by = ? WHERE id = ? "
                "AND parked_by IS NULL", (keeper["id"], r["id"]))
            stats["parked"] += 1
        stats["bursts"] += 1
        stats["kept"] += 1
    conn.commit()
    finish_run(conn, run, stats)
    return stats


def release(conn, photo_id: int) -> bool:
    """Bring a parked photo back (it re-enters the pipeline at 'staged', so it
    is screened before anything shows it). Returns False if it wasn't parked."""
    cur = conn.execute(
        "UPDATE photos SET status = 'staged', parked_by = NULL, burst_released = 1 WHERE id = ? "
        "AND status = 'parked'", (photo_id,))
    conn.commit()
    return cur.rowcount == 1
