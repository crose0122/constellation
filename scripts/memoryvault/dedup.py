"""Stage 3 — Near-duplicate detection (SPEC.md §5.3).

Exact duplicates are already handled at ingest (same sha256 → files row).
Near-dups: Hamming(pHash) <= threshold, clustered by union-find. Candidate
pairs come from pigeonhole bucketing — a 64-bit pHash split into 16 nibbles;
two hashes within distance 15 must share at least one (position, nibble),
so for threshold <= 15 bucketing is exact, not approximate."""

import shutil
from collections import defaultdict
from pathlib import Path

import imagehash

from . import config
from .db import start_run, finish_run


def _candidate_pairs(hashes: dict[int, imagehash.ImageHash]):
    buckets = defaultdict(list)
    for pid, h in hashes.items():
        hx = str(h)
        for i, nibble in enumerate(hx):
            buckets[(i, nibble)].append(pid)
    seen = set()
    for members in buckets.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pair = (min(members[i], members[j]), max(members[i], members[j]))
                if pair not in seen:
                    seen.add(pair)
                    yield pair


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

    for a, b in _candidate_pairs(hashes):
        if hashes[a] - hashes[b] <= threshold:
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
