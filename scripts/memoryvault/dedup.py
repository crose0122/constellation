"""Stage 3 — Near-duplicate detection (SPEC.md §5.3).

Exact duplicates are already handled at ingest (same sha256 → files row).
Near-dups: Hamming(pHash) <= threshold, clustered by union-find.

Distances come from a packed popcount sweep: a pHash is 8 bytes, so the whole
library fits in a numpy array and each chunk of rows is one XOR against it.
This replaced pigeonhole bucketing (16 nibble positions, candidate pairs held
in a `seen` set), which at 21k photos enumerated ~150M pairs and is what
OOM-killed `mvault edges` on 2026-08-01 and 2026-08-02. The sweep is exact
at every distance — the bucketing was only exact for threshold <= 15."""

import shutil
from collections import defaultdict
from pathlib import Path

import imagehash
import numpy as np

from . import config
from .db import start_run, finish_run

# rows of the distance matrix held at once: at 22k photos one chunk is
# ~45MB of intermediates, and nothing outlives the loop iteration
CHUNK = 256

_POPCOUNT = np.unpackbits(
    np.arange(256, dtype=np.uint8)[:, None], axis=1
).sum(axis=1).astype(np.int16)

_FAR = np.int16(1000)  # sentinel: "outside the band we asked for"


def _distance_rows(hashes: dict[int, imagehash.ImageHash]):
    """Yield (row_offset, ids, distances) — distances[r][c] is the Hamming
    distance from ids[row_offset + r] to ids[c]. ids is sorted ascending."""
    ids = np.array(sorted(hashes), dtype=np.int64)
    if len(ids) == 0:
        return
    bits = np.array([hashes[int(pid)].hash.flatten() for pid in ids], dtype=np.uint8)
    packed = np.packbits(bits, axis=1)
    for start in range(0, len(ids), CHUNK):
        block = packed[start:start + CHUNK]
        d = _POPCOUNT[block[:, None, :] ^ packed[None, :, :]].sum(axis=2)
        yield start, ids, d.astype(np.int16)


def pairs_within(hashes: dict[int, imagehash.ImageHash], threshold: int):
    """Every unordered pair whose pHash distance is <= threshold, once each."""
    for start, ids, d in _distance_rows(hashes):
        rows, cols = np.nonzero(d <= threshold)
        for r, c in zip(rows, cols):
            a, b = int(ids[start + int(r)]), int(ids[int(c)])
            if a < b:  # also drops the self-pair on the diagonal
                yield a, b


def nearest_neighbors(hashes: dict[int, imagehash.ImageHash], lo: int, hi: int,
                      k: int):
    """The k closest photos to each photo within lo < distance <= hi.

    Bounded output (k per photo) for callers that prune to top-K anyway —
    yielding every pair in the band just to throw most of them away is what
    made edge computation quadratic in memory."""
    for start, ids, d in _distance_rows(hashes):
        band = np.where((d > lo) & (d <= hi), d, _FAR)
        take = min(k, band.shape[1] - 1)
        if take < 1:
            return
        idx = np.argpartition(band, take, axis=1)[:, :take]
        for r in range(band.shape[0]):
            a = int(ids[start + r])
            for c in idx[r]:
                dist = int(band[r, int(c)])
                if dist <= hi:  # _FAR entries are padding, not neighbours
                    yield a, int(ids[int(c)]), dist


def find_near_groups(conn, threshold: int) -> list[list[int]]:
    rows = conn.execute(
        "SELECT id, phash FROM photos WHERE phash IS NOT NULL "
        "AND status IN ('staged','screened','tagged','noted')"
    ).fetchall()
    hashes = {r["id"]: imagehash.hex_to_hash(r["phash"]) for r in rows}

    parent = {pid: pid for pid in hashes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs_within(hashes, threshold):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    clusters = defaultdict(list)
    for pid in hashes:
        clusters[find(pid)].append(pid)
    return [members for members in clusters.values() if len(members) > 1]


def pick_keeper(conn, photo_ids: list[int]) -> int:
    rows = conn.execute(
        f"SELECT id, COALESCE(width,0)*COALESCE(height,0) AS res "
        f"FROM photos WHERE id IN ({','.join('?' * len(photo_ids))})",
        photo_ids,
    ).fetchall()
    return max(rows, key=lambda r: r["res"])["id"]


def quarantine_losers(conn, group_id: int, keeper: int, losers: list[int]):
    """Move losers' library copies to duplicates/; nothing is deleted without
    a human decision recorded in duplicate_members."""
    qdir = config.LIBRARY_ROOT / "duplicates"
    qdir.mkdir(parents=True, exist_ok=True)
    for pid in losers:
        photo = conn.execute("SELECT * FROM photos WHERE id = ?", (pid,)).fetchone()
        if not photo or not photo["library_path"]:
            continue
        src = config.LIBRARY_ROOT / photo["library_path"]
        if src.exists():
            dst = qdir / src.name
            if dst.exists():
                dst = qdir / f"{photo['sha256'][:8]}-{src.name}"
            shutil.move(str(src), str(dst))
            conn.execute(
                "UPDATE photos SET library_path = ?, status = 'quarantined' WHERE id = ?",
                (str(dst.relative_to(config.LIBRARY_ROOT)), pid),
            )
        file_row = conn.execute(
            "SELECT id FROM files WHERE photo_id = ? LIMIT 1", (pid,)
        ).fetchone()
        if file_row:
            conn.execute(
                "INSERT OR IGNORE INTO duplicate_members(group_id, file_id) VALUES (?,?)",
                (group_id, file_row["id"]),
            )


def dedup(conn, threshold: int | None = None, quarantine: bool = False) -> dict:
    threshold = threshold if threshold is not None else config.NEAR_DUP_THRESHOLD
    run = start_run(conn, "dedup")
    groups = find_near_groups(conn, threshold)
    stats = {"near_groups": len(groups), "quarantined": 0}

    for members in groups:
        keeper = pick_keeper(conn, members)
        existing = conn.execute(
            "SELECT id FROM duplicate_groups WHERE kind='near' AND keeper_photo_id=?",
            (keeper,),
        ).fetchone()
        if existing:
            gid = existing["id"]
        else:
            gid = conn.execute(
                "INSERT INTO duplicate_groups(kind, keeper_photo_id) VALUES ('near', ?)",
                (keeper,),
            ).lastrowid
        losers = [m for m in members if m != keeper]
        if quarantine:
            quarantine_losers(conn, gid, keeper, losers)
            stats["quarantined"] += len(losers)

    conn.commit()
    finish_run(conn, run, stats)
    return stats
