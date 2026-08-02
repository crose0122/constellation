"""Memory-graph edge computation (SPEC.md §9.1 / v1.3).

All relations derive from data the pipeline already computed — tags, EXIF,
pHash, and (when present) CLIP embeddings. Precomputed at ingest/tag time,
never on click. Vault content cannot appear here: it has no DB rows.
"""

from collections import defaultdict

from .db import start_run, finish_run

TOP_K = 8  # per relation per node — dense graphs are noise, not browsing

# neighbours generated per photo inside one group (tag, day, month). Every
# pair in a group carries the same weight, so all-pairs generation just fed
# the top-K prune arbitrary ties at n²/2 cost: four people tags alone are
# ~10M pairs, and holding them OOM-killed the run on 2026-08-01/02. Linking
# each photo to its nearest-in-time group-mates is bounded AND browsable.
# Kept above TOP_K so the prune still has ties to choose from.
GROUP_FANOUT = 12

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


def _chronological(conn) -> dict[int, str]:
    """photo_id -> sort key. Undated photos sort last, then by id, so the
    ordering is total and stable across runs."""
    return {
        row["id"]: row["taken_at"] or "9999"
        for row in conn.execute("SELECT id, taken_at FROM photos")
    }


def _neighbour_pairs(pids, order: dict[int, str], fanout: int = GROUP_FANOUT):
    """Pairs inside one group, capped at `fanout` per photo.

    Yields at most fanout * len(pids) pairs instead of len(pids)²/2, and
    because the group is walked in time order the pairs it does yield are
    the ones a person would want to browse: what came next."""
    ordered = sorted(set(pids), key=lambda pid: (order.get(pid, "9999"), pid))
    for i, a in enumerate(ordered):
        for b in ordered[i + 1: i + 1 + fanout]:
            yield a, b


def compute_edges(conn) -> dict:
    run = start_run(conn, "edges")
    edges: dict[tuple, float] = {}
    order = _chronological(conn)

    # same-person: shared People tag (face clusters replace this in v1.4)
    for person, pids in _tag_map(conn, "people").items():
        for a, b in _neighbour_pairs(pids, order):
            _add(edges, a, b, "same-person", 1.0)

    # same-place: shared Location tag (offline geocode refines later)
    for place, pids in _tag_map(conn, "location").items():
        if place in ("Indoor", "Outdoor", "Unknown"):
            continue  # too generic to be a pathway
        for a, b in _neighbour_pairs(pids, order):
            _add(edges, a, b, "same-place", 0.8)

    # same-event / near-time from taken_at
    by_day, by_month = defaultdict(list), defaultdict(list)
    for row in conn.execute(
        "SELECT id, taken_at FROM photos WHERE taken_at IS NOT NULL "
        "AND status IN ('screened','tagged','noted')"
    ):
        by_day[row["taken_at"][:10]].append(row["id"])
        by_month[row["taken_at"][:7]].append(row["id"])
    for day, pids in by_day.items():
        for a, b in _neighbour_pairs(pids, order):
            _add(edges, a, b, "same-event", 0.9)
    # near-time links ACROSS days within a month — one representative per day,
    # or it just re-states same-event at a lower weight. (The old code skipped
    # any month with >30 photos, so in practice this relation barely existed.)
    for month, pids in by_month.items():
        first_of_day = {}
        for pid in sorted(pids, key=lambda p: (order.get(p, "9999"), p)):
            first_of_day.setdefault(order.get(pid, "9999")[:10], pid)
        for a, b in _neighbour_pairs(first_of_day.values(), order, fanout=4):
            _add(edges, a, b, "near-time", 0.3)

    # similar: CLIP embeddings when present (v1.2); until then, close pHash
    import imagehash

    from .dedup import nearest_neighbors

    rows = conn.execute(
        "SELECT id, phash FROM photos WHERE phash IS NOT NULL "
        "AND status IN ('screened','tagged','noted')"
    ).fetchall()
    hashes = {r["id"]: imagehash.hex_to_hash(r["phash"]) for r in rows}
    # 10 < d: nearer than that is a near-dup, which is dedup's job, not an edge
    for a, b, d in nearest_neighbors(hashes, lo=10, hi=22, k=TOP_K):
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

    # fanout is reported so a thin graph reads as "capped here", not "broken"
    stats = {"edges": len(keep), "raw_pairs": len(edges), "fanout": GROUP_FANOUT}
    finish_run(conn, run, stats)
    return stats
