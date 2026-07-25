"""Memory-graph edge computation (SPEC.md §9.1 / v1.3).

All relations derive from data the pipeline already computed — tags, EXIF,
pHash, and (when present) CLIP embeddings. Precomputed at ingest/tag time,
never on click. Vault content cannot appear here: it has no DB rows.
"""

from collections import defaultdict
from itertools import combinations

from .db import start_run, finish_run

TOP_K = 8  # per relation per node — dense graphs are noise, not browsing

RELATIONS = ("same-person", "same-place", "same-event", "near-time", "similar")


def _tag_map(conn, dimension: str) -> dict[str, list[int]]:
    out = defaultdict(list)
    for row in conn.execute(
        "SELECT photo_id, value FROM tags WHERE dimension = ?", (dimension,)
    ):
        out[row["value"]].append(row["photo_id"])
    return out


def _add(edges, a, b, relation, weight):
    if a == b:
        return
    key = (min(a, b), max(a, b), relation)
    if weight > edges.get(key, 0.0):
        edges[key] = weight


def compute_edges(conn) -> dict:
    run = start_run(conn, "edges")
    edges: dict[tuple, float] = {}

    # same-person: shared People tag (face clusters replace this in v1.4)
    for person, pids in _tag_map(conn, "people").items():
        for a, b in combinations(pids, 2):
            _add(edges, a, b, "same-person", 1.0)

    # same-place: shared Location tag (offline geocode refines later)
    for place, pids in _tag_map(conn, "location").items():
        if place in ("Indoor", "Outdoor", "Unknown"):
            continue  # too generic to be a pathway
        for a, b in combinations(pids, 2):
            _add(edges, a, b, "same-place", 0.8)

    # same-event / near-time from taken_at
    by_day, by_week = defaultdict(list), defaultdict(list)
    for row in conn.execute(
        "SELECT id, taken_at FROM photos WHERE taken_at IS NOT NULL "
        "AND status IN ('screened','tagged','noted')"
    ):
        day = row["taken_at"][:10]
        by_day[day].append(row["id"])
        by_week[row["taken_at"][:7]].append(row["id"])
    for day, pids in by_day.items():
        for a, b in combinations(pids, 2):
            _add(edges, a, b, "same-event", 0.9)
    for month, pids in by_week.items():
        if len(pids) <= 30:  # skip mega-buckets
            for a, b in combinations(pids, 2):
                _add(edges, a, b, "near-time", 0.3)

    # similar: CLIP embeddings when present (v1.2); until then, close pHash
    import imagehash

    rows = conn.execute(
        "SELECT id, phash FROM photos WHERE phash IS NOT NULL "
        "AND status IN ('screened','tagged','noted')"
    ).fetchall()
    hashes = {r["id"]: imagehash.hex_to_hash(r["phash"]) for r in rows}
    from .dedup import _candidate_pairs

    for a, b in _candidate_pairs(hashes):
        d = hashes[a] - hashes[b]
        if 10 < d <= 22:  # near-dup range is dedup's job, not an edge
            _add(edges, a, b, "similar", 1.0 - d / 32)

    # prune to top-K per (node, relation)
    per_node = defaultdict(list)
    for (a, b, rel), w in edges.items():
        per_node[(a, rel)].append((w, a, b))
        per_node[(b, rel)].append((w, a, b))
    keep = set()
    for (_, rel), lst in per_node.items():
        for w, a, b in sorted(lst, reverse=True)[:TOP_K]:
            keep.add((a, b, rel))

    conn.execute("DELETE FROM photo_edges")
    conn.executemany(
        "INSERT OR REPLACE INTO photo_edges(photo_id_a, photo_id_b, relation, weight) "
        "VALUES (?,?,?,?)",
        [(a, b, rel, edges[(a, b, rel)]) for (a, b, rel) in keep],
    )
    conn.commit()

    stats = {"edges": len(keep), "raw_pairs": len(edges)}
    finish_run(conn, run, stats)
    return stats
