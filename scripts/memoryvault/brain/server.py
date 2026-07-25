"""The Brain — LAN web server (SPEC.md §9.1). Stdlib only, read-only DB.

Endpoints:
  GET /                          explore mode
  GET /ambient                   ambient brain-wall mode
  GET /api/start?mode=...        pick a seed neuron (on-this-day weighted)
  GET /api/neighborhood?id=&k=   focused node + 1-hop neighbors + edges
  GET /api/photo?id=             side-panel detail (tags, why-related)
  GET /thumb/<sha16>.jpg         thumbnail
"""

import json
import random
import sqlite3
import sys
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .. import config
from ..db import connect

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_K = 8


def _node(row) -> dict:
    return {
        "id": row["id"],
        "thumb": f"/thumb/{row['sha256'][:16]}.jpg",
        "display": f"/display/{row['sha256'][:16]}.jpg",
        "taken_at": row["taken_at"],
        "label": (row["taken_at"] or "")[:10],
    }


DISPLAY_MAX_PX = 2048


def _display_rendition(conn, sha16: str) -> Path | None:
    """Full-screen rendition: the original downscaled to <= 1600px JPEG,
    generated on first request and cached. Re-encoding also makes HEIC
    originals viewable in every browser."""
    out = config.LIBRARY_ROOT / "display" / f"{sha16}.jpg"
    if out.exists():
        return out
    row = conn.execute(
        "SELECT library_path FROM photos WHERE sha256 LIKE ?", (sha16 + "%",)
    ).fetchone()
    if not row or not row["library_path"]:
        return None
    src = config.LIBRARY_ROOT / row["library_path"]
    if not src.exists():
        return None
    from PIL import Image, ImageOps

    from ..describe import apply_orientation

    orientation = None
    try:
        o = conn.execute(
            "SELECT d.orientation FROM descriptions d JOIN photos p "
            "ON p.id = d.photo_id WHERE p.sha256 LIKE ?", (sha16 + "%",)
        ).fetchone()
        orientation = o["orientation"] if o else None
    except sqlite3.OperationalError:
        pass  # descriptions table not created yet
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im = apply_orientation(im, orientation)
        im.thumbnail((DISPLAY_MAX_PX, DISPLAY_MAX_PX))
        im.save(out, "JPEG", quality=88)
    return out


class BrainDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def conn(self):
        return connect(self.db_path, readonly=True)

    # Category graph (the default view): neurons are tag values, dendrite
    # weights are photo co-occurrence counts. "None"-ish values are noise.
    _SKIP_VALUES = ("none", "unknown", "n/a", "", "no pets", "not funny",
                    "important", "good", "neutral", "casual", "solo",
                    "kept", "delete", "removed", "posing")

    _cat_cache: dict = {}

    def categories(self, conn, top: int = 40, min_count: int = 2, k: int = 6) -> dict:
        import time as _t

        ck = (top, min_count, k)
        hit = self._cat_cache.get(ck)
        if hit and _t.time() - hit[0] < 600:
            return hit[1]
        rows = conn.execute(
            "SELECT dimension, value, COUNT(*) AS c FROM tags "
            "WHERE dimension != 'curation_reason' "
            "GROUP BY dimension, value HAVING c >= ? ORDER BY c DESC LIMIT ?",
            (min_count, top * 2),
        ).fetchall()
        nodes = [
            {"key": f"{r['dimension']}:{r['value']}", "dim": r["dimension"],
             "value": r["value"], "count": r["c"]}
            for r in rows if r["value"].strip().lower() not in self._SKIP_VALUES
        ][:top]
        keys = {n["key"] for n in nodes}
        pairs = conn.execute(
            "SELECT t1.dimension d1, t1.value v1, t2.dimension d2, t2.value v2, "
            "COUNT(*) w FROM tags t1 JOIN tags t2 ON t1.photo_id = t2.photo_id "
            "AND (t1.dimension || ':' || t1.value) < (t2.dimension || ':' || t2.value) "
            "GROUP BY 1, 2, 3, 4 HAVING w >= 2 ORDER BY w DESC"
        ).fetchall()
        per_node: dict[str, int] = {}
        edges = []
        for p in pairs:
            a, b = f"{p['d1']}:{p['v1']}", f"{p['d2']}:{p['v2']}"
            if a not in keys or b not in keys:
                continue
            if per_node.get(a, 0) >= k and per_node.get(b, 0) >= k:
                continue
            per_node[a] = per_node.get(a, 0) + 1
            per_node[b] = per_node.get(b, 0) + 1
            edges.append({"a": a, "b": b, "weight": p["w"]})
        result = {"nodes": nodes, "edges": edges}
        self._cat_cache[ck] = (_t.time(), result)
        return result

    def category_photos(self, conn, dim: str, value: str, limit: int = 48,
                        offset: int = 0) -> dict:
        rows = conn.execute(
            "SELECT p.* FROM photos p JOIN tags t ON t.photo_id = p.id "
            "WHERE t.dimension = ? AND t.value = ? "
            "ORDER BY p.taken_at IS NULL, p.taken_at DESC LIMIT ? OFFSET ?",
            (dim, value, limit, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) c FROM tags WHERE dimension = ? AND value = ?",
            (dim, value),
        ).fetchone()["c"]
        return {"dim": dim, "value": value, "count": total,
                "photos": [_node(r) for r in rows]}

    _NOT_TRASH = (
        "AND id NOT IN (SELECT photo_id FROM tags "
        "WHERE dimension = 'curation' AND value IN ('Trash','Removed','Delete')) "
    )

    def progress(self, conn) -> dict:
        q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        discovered = q("SELECT COUNT(*) FROM files")
        ingested = q("SELECT COUNT(*) FROM photos")
        screened = q("SELECT COUNT(*) FROM photos "
                     "WHERE status IN ('screened','tagged','noted')")
        tagged = q("SELECT COUNT(*) FROM photos WHERE status IN ('tagged','noted')")
        trash = q("SELECT COUNT(DISTINCT photo_id) FROM tags "
                  "WHERE dimension = 'curation' AND value = 'Trash'")
        vaulted = int((conn.execute(
            "SELECT value FROM stats WHERE key = 'vaulted_total'"
        ).fetchone() or [0])[0])
        review = int((conn.execute(
            "SELECT value FROM stats WHERE key = 'review_total'"
        ).fetchone() or [0])[0])
        errors = q("SELECT COUNT(*) FROM errors WHERE resolved = 0")
        active = conn.execute(
            "SELECT stage, started_at FROM runs WHERE finished_at IS NULL "
            "ORDER BY id DESC LIMIT 1").fetchone()
        # quality sweeps: done vs live totals (photos can leave a sweep's
        # queue mid-run — vaulted, purged — so totals are computed fresh)
        visible = ("FROM photos WHERE status IN ('screened','tagged','noted') "
                   "AND library_path IS NOT NULL ")
        not_trash = ("AND id NOT IN (SELECT photo_id FROM tags WHERE "
                     "dimension = 'curation' AND value = 'Trash') ")
        retag_done = q("SELECT COUNT(DISTINCT photo_id) FROM tags "
                       "WHERE model_version LIKE '%@schema-2.0'")
        retag_total = q(f"SELECT COUNT(*) {visible}{not_trash}")
        rescue_done = q("SELECT COUNT(*) FROM tags "
                        "WHERE dimension = 'curation_check'")
        rescue_total = rescue_done + q(
            f"SELECT COUNT(*) {visible}"
            "AND id IN (SELECT photo_id FROM tags WHERE "
            " dimension = 'curation' AND value = 'Trash') "
            "AND id NOT IN (SELECT photo_id FROM tags WHERE "
            " dimension = 'curation_check')")
        rescreen_done = q("SELECT COUNT(*) FROM tags "
                          "WHERE dimension = 'screen_check'")
        rescreen_total = rescreen_done + q(
            f"SELECT COUNT(*) {visible}"
            "AND id NOT IN (SELECT photo_id FROM tags WHERE "
            " dimension = 'screen_check')")
        try:
            desc_done = q("SELECT COUNT(*) FROM descriptions")
            desc_total = desc_done + q(
                f"SELECT COUNT(*) {visible}{not_trash}"
                "AND id NOT IN (SELECT photo_id FROM descriptions)")
        except sqlite3.OperationalError:
            desc_done, desc_total = 0, 0
        sweeps = [
            {"key": "retag", "label": "Re-tag (schema v2)",
             "done": retag_done, "total": retag_total},
            {"key": "rescue", "label": "Trash rescue (7B review)",
             "done": rescue_done, "total": rescue_total},
            {"key": "rescreen", "label": "NSFW re-screen",
             "done": rescreen_done, "total": rescreen_total},
            {"key": "describe", "label": "Captions + OCR + orientation",
             "done": desc_done, "total": desc_total},
        ]
        return {
            "sweeps": sweeps,
            "discovered": discovered,
            "ingested": ingested,
            "screen_done": screened + vaulted + review,
            "screen_total": ingested,
            "tag_done": tagged,
            "tag_total": max(0, ingested - trash),
            "trash": trash, "vaulted": vaulted, "review": review,
            "errors": errors,
            "active_stage": active["stage"] if active else None,
        }

    def start_node(self, conn) -> int | None:
        # on-this-day is a nice seed SOMETIMES — preferring it always meant
        # every dead-ended walk reseeded from the same two dozen anniversary
        # photos and the show looped a handful of pictures forever
        row = None
        if random.random() < 0.3:
            today = datetime.now().strftime("%m-%d")
            row = conn.execute(
                "SELECT id FROM photos WHERE status IN ('screened','tagged','noted') "
                + self._NOT_TRASH +
                "AND substr(taken_at, 6, 5) = ? ORDER BY RANDOM() LIMIT 1",
                (today,),
            ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT id FROM photos WHERE status IN ('screened','tagged','noted') "
                + self._NOT_TRASH + "ORDER BY RANDOM() LIMIT 1"
            ).fetchone()
        return row["id"] if row else None

    def neighborhood(self, conn, photo_id: int, k: int) -> dict:
        focus = conn.execute(
            "SELECT * FROM photos WHERE id = ?", (photo_id,)
        ).fetchone()
        if not focus:
            return {"nodes": [], "edges": [], "focus": None}

        edges = conn.execute(
            "SELECT * FROM photo_edges WHERE photo_id_a = ? OR photo_id_b = ? "
            "ORDER BY weight DESC",
            (photo_id, photo_id),
        ).fetchall()

        per_rel: dict[str, list] = {}
        picked = []
        for e in edges:
            rel = e["relation"]
            per_rel.setdefault(rel, [])
            if len(per_rel[rel]) < k:
                per_rel[rel].append(e)
                picked.append(e)

        ids = {photo_id}
        for e in picked:
            ids.add(e["photo_id_a"])
            ids.add(e["photo_id_b"])
        rows = conn.execute(
            f"SELECT * FROM photos WHERE id IN ({','.join('?' * len(ids))})",
            list(ids),
        ).fetchall()

        # edges among the neighbor set too, so the local web looks alive
        extra = conn.execute(
            f"SELECT * FROM photo_edges WHERE photo_id_a IN ({','.join('?' * len(ids))}) "
            f"AND photo_id_b IN ({','.join('?' * len(ids))})",
            list(ids) * 2,
        ).fetchall()

        return {
            "focus": photo_id,
            "nodes": [_node(r) for r in rows],
            "edges": [
                {"a": e["photo_id_a"], "b": e["photo_id_b"],
                 "relation": e["relation"], "weight": e["weight"]}
                for e in extra
            ],
        }

    def photo_detail(self, conn, photo_id: int) -> dict:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row:
            return {}
        tags = {}
        for t in conn.execute(
            "SELECT dimension, value FROM tags WHERE photo_id = ?", (photo_id,)
        ):
            tags.setdefault(t["dimension"], []).append(t["value"])
        relations = {}
        for e in conn.execute(
            "SELECT relation, COUNT(*) AS n FROM photo_edges "
            "WHERE photo_id_a = ? OR photo_id_b = ? GROUP BY relation",
            (photo_id, photo_id),
        ):
            relations[e["relation"]] = e["n"]
        return {**_node(row), "tags": tags, "relations": relations}


class Handler(BaseHTTPRequestHandler):
    braindb: BrainDB = None  # set by serve()

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Thumbs/renditions are content-addressed (sha-named) — cache hard.
        # Everything else must revalidate: mobile Chrome's heuristic caching
        # served stale app JS through reloads without this.
        if self.path.startswith(("/thumb/", "/display/")):
            self.send_header("Cache-Control", "public, max-age=604800, immutable")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        try:
            conn = self.braindb.conn()
        except sqlite3.OperationalError as e:
            # JSON so the frontend's res.ok/json() path degrades cleanly
            print(f"[brain] 503 db unavailable: {e}", file=sys.stderr, flush=True)
            self._send(503, b'{"error": "db unavailable"}', "application/json")
            return
        try:
            if url.path in ("/", "/ambient", "/memories"):
                page = (STATIC_DIR / "index.html").read_text()
                # stamp asset URLs with their mtime: a reloaded page always
                # references the current JS/CSS, defeating mobile Chrome's
                # heuristic subresource cache (stale brain.js bit us twice)
                for asset in ("brain.css", "brain.js"):
                    v = int((STATIC_DIR / asset).stat().st_mtime)
                    page = page.replace(f"/static/{asset}", f"/static/{asset}?v={v}")
                self._send(200, page.encode(), "text/html")
            elif url.path == "/manifest.json":
                self._send(200, (STATIC_DIR / "manifest.json").read_bytes(),
                           "application/manifest+json")
            elif url.path == "/sw.js":
                # served from the root so the service worker scope covers "/"
                self._send(200, (STATIC_DIR / "sw.js").read_bytes(),
                           "application/javascript")
            elif url.path.startswith("/static/"):
                f = STATIC_DIR / Path(url.path).name
                if f.exists():
                    ctype = ("text/css" if f.suffix == ".css" else
                             "image/png" if f.suffix == ".png" else
                             "application/javascript")
                    self._send(200, f.read_bytes(), ctype)
                else:
                    self._send(404, b"not found", "text/plain")
            elif url.path == "/api/dbg":
                ua = self.headers.get("User-Agent", "?")
                with open("/tmp/brain-dbg.log", "a") as fh:
                    fh.write(f"{url.query} UA={ua}\n")
                self._send(204, b"", "text/plain")
            elif url.path == "/people":
                page = (STATIC_DIR / "people.html").read_bytes()
                self._send(200, page, "text/html")
            elif url.path == "/api/people":
                rows = conn.execute(
                    "SELECT c.id, c.label, COUNT(f.id) n, "
                    " (SELECT f2.id FROM faces f2 WHERE f2.cluster_id = c.id "
                    "   ORDER BY f2.quality DESC LIMIT 1) fid "
                    "FROM face_clusters c JOIN faces f ON f.cluster_id = c.id "
                    "GROUP BY c.id HAVING n >= 2 ORDER BY n DESC LIMIT 120"
                ).fetchall()
                self._json({"clusters": [
                    {"id": r["id"], "label": r["label"], "count": r["n"],
                     "face": f"/face/{r['fid']}.jpg" if r["fid"] else None}
                    for r in rows]})
            elif url.path == "/person":
                page = (STATIC_DIR / "person.html").read_bytes()
                self._send(200, page, "text/html")
            elif url.path == "/api/person":
                # every face crop currently driving this name's people tag
                label = q.get("label", [""])[0].strip()
                rows = conn.execute(
                    "SELECT f.id, f.photo_id, p.sha256 "
                    "FROM faces f JOIN face_clusters c ON c.id = f.cluster_id "
                    "JOIN photos p ON p.id = f.photo_id "
                    "WHERE c.label = ? ORDER BY f.quality DESC LIMIT 1200",
                    (label,)).fetchall()
                self._json({"label": label, "faces": [
                    {"id": r["id"], "face": f"/face/{r['id']}.jpg",
                     "photo": f"/display/{r['sha256'][:16]}.jpg"}
                    for r in rows]})
            elif url.path == "/api/people/notthem":
                from ..faces import ban_face

                fid = int(q["face"][0])
                wconn = connect(config.DB_PATH)
                try:
                    self._json(ban_face(wconn, fid))
                finally:
                    wconn.close()
            elif url.path == "/api/people/removeall":
                # bulk-remove a labeled person's photos to the restorable
                # Removed bin. mode=solo touches only photos where every
                # detected face is theirs (family shots with the kids stay);
                # mode=all takes every photo carrying their people tag or a
                # face in their clusters. dry=1 just counts.
                label = q.get("label", [""])[0].strip()
                mode = q.get("mode", ["solo"])[0]
                dry = q.get("dry", ["0"])[0] == "1"
                if not label:
                    self._send(400, b'{"error": "label required"}',
                               "application/json")
                    return
                cids = [r["id"] for r in conn.execute(
                    "SELECT id FROM face_clusters WHERE label = ?",
                    (label,)).fetchall()]
                inc = ",".join("?" * len(cids)) or "NULL"
                if mode == "solo":
                    if not cids:
                        self._json({"label": label, "mode": mode, "count": 0})
                        return
                    rows = conn.execute(
                        f"SELECT photo_id FROM faces GROUP BY photo_id "
                        f"HAVING SUM(CASE WHEN cluster_id IN ({inc}) "
                        f"  THEN 1 ELSE 0 END) > 0 "
                        f"AND SUM(CASE WHEN cluster_id IN ({inc}) "
                        f"  THEN 0 ELSE 1 END) = 0", (*cids, *cids)).fetchall()
                else:
                    rows = conn.execute(
                        f"SELECT DISTINCT photo_id FROM ("
                        f"SELECT photo_id FROM tags WHERE dimension='people' "
                        f"  AND value = ? "
                        f"UNION SELECT photo_id FROM faces "
                        f"  WHERE cluster_id IN ({inc}))",
                        (label, *cids)).fetchall()
                pids = [r["photo_id"] for r in rows]
                if dry:
                    self._json({"label": label, "mode": mode,
                                "count": len(pids)})
                    return
                wconn = connect(config.DB_PATH)
                try:
                    for pid in pids:
                        wconn.execute(
                            "DELETE FROM tags WHERE photo_id = ? AND "
                            "dimension IN ('curation','curation_reason')",
                            (pid,))
                        wconn.execute(
                            "INSERT OR IGNORE INTO tags (photo_id, dimension, "
                            "value, confidence, model_version) VALUES "
                            "(?, 'curation', 'Removed', 1.0, 'user-1.0')",
                            (pid,))
                        wconn.execute(
                            "INSERT OR IGNORE INTO tags (photo_id, dimension, "
                            "value, confidence, model_version) VALUES "
                            "(?, 'curation_reason', ?, 1.0, 'user-1.0')",
                            (pid, f"person-removed:{label}"))
                    wconn.commit()
                finally:
                    wconn.close()
                self._json({"label": label, "mode": mode,
                            "removed": len(pids)})
            elif url.path == "/api/people/label":
                from ..faces import label_cluster

                cid = int(q["cluster"][0])
                label = q.get("label", [""])[0].strip()
                wconn = connect(config.DB_PATH)
                try:
                    self._json(label_cluster(wconn, cid, label))
                finally:
                    wconn.close()
            elif url.path == "/gallery":
                page = (STATIC_DIR / "gallery.html").read_bytes()
                self._send(200, page, "text/html")
            elif url.path == "/api/gallery":
                off = int(q.get("offset", [0])[0])
                lim = min(200, int(q.get("limit", [120])[0]))
                search = (q.get("search", [""])[0] or "").strip()
                where = (
                    "WHERE status IN ('screened','tagged','noted') AND id NOT IN "
                    "  (SELECT photo_id FROM tags WHERE dimension = 'curation' "
                    "   AND value IN ('Trash','Removed','Delete')) "
                )
                params: list = []
                # every space-separated term must match a tag value, a
                # dimension name, or the photo's date — AND semantics, so
                # "alex birthday 2019" narrows the way you'd expect
                has_desc = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='descriptions'").fetchone()
                for term in search.split()[:6]:
                    where += (
                        "AND (id IN (SELECT photo_id FROM tags WHERE "
                        "  dimension != 'curation_reason' AND "
                        "  value NOT IN ('No Pets', 'Not Funny', 'Important', "
                        "  'Good', 'Neutral', 'Casual', 'Solo', 'Posing') AND "
                        "  (value LIKE ? OR dimension LIKE ?)) "
                        "  OR taken_at LIKE ? "
                    )
                    like = f"%{term}%"
                    params += [like, like, like]
                    if has_desc:
                        # free-text layer: captions + text read off the photo
                        where += (
                            "OR id IN (SELECT photo_id FROM descriptions "
                            "  WHERE caption LIKE ? OR ocr_text LIKE ?) "
                        )
                        params += [like, like]
                    where += ") "
                order_sql = ("ORDER BY RANDOM() "
                             if q.get("order", [""])[0] == "random" else
                             "ORDER BY taken_at IS NULL, taken_at DESC, "
                             "id DESC ")
                rows = conn.execute(
                    f"SELECT id, sha256, taken_at FROM photos {where} "
                    + order_sql
                    + "LIMIT ? OFFSET ?", (*params, lim, off)).fetchall()
                total = conn.execute(
                    f"SELECT COUNT(*) c FROM photos {where}", params
                ).fetchone()["c"]
                self._json({"total": total, "offset": off, "photos": [
                    {"id": r["id"], "thumb": f"/thumb/{r['sha256'][:16]}.jpg",
                     "display": f"/display/{r['sha256'][:16]}.jpg",
                     "taken_at": r["taken_at"]} for r in rows]})
            elif url.path == "/node":
                page = (STATIC_DIR / "node.html").read_bytes()
                self._send(200, page, "text/html")
            elif url.path == "/curation":
                page = (STATIC_DIR / "curation.html").read_bytes()
                self._send(200, page, "text/html")
            elif url.path == "/api/purge":
                # PERMANENT deletion of everything in a bin. Requires the
                # typed confirmation to have reached the client first.
                binv = q.get("bin", [""])[0]
                if binv not in ("Trash", "Removed", "Delete") or \
                        q.get("confirm", [""])[0] != "DELETE":
                    self._send(400, b'{"error": "bad request"}',
                               "application/json")
                else:
                    wconn = connect(config.DB_PATH)
                    try:
                        wconn.execute(
                            "CREATE TABLE IF NOT EXISTS purged ("
                            "sha256 TEXT PRIMARY KEY, ts TEXT)")
                        rows = wconn.execute(
                            "SELECT p.id, p.sha256, p.library_path FROM "
                            "photos p JOIN tags t ON t.photo_id = p.id "
                            "WHERE t.dimension = 'curation' AND t.value = ?",
                            (binv,)).fetchall()
                        n = 0
                        for r in rows:
                            for f in [
                                config.LIBRARY_ROOT / (r["library_path"] or "x"),
                                config.LIBRARY_ROOT / "thumbnails" /
                                    f"{r['sha256'][:16]}.jpg",
                                config.LIBRARY_ROOT / "display" /
                                    f"{r['sha256'][:16]}.jpg",
                            ]:
                                try:
                                    f.unlink()
                                except OSError:
                                    pass
                            for fc in wconn.execute(
                                    "SELECT id FROM faces WHERE photo_id = ?",
                                    (r["id"],)).fetchall():
                                try:
                                    (config.LIBRARY_ROOT / "face-crops" /
                                     f"{fc['id']}.jpg").unlink()
                                except OSError:
                                    pass
                            for tbl, col in (("tags", "photo_id"),
                                             ("faces", "photo_id"),
                                             ("files", "photo_id")):
                                wconn.execute(
                                    f"DELETE FROM {tbl} WHERE {col} = ?",
                                    (r["id"],))
                            wconn.execute(
                                "DELETE FROM photo_edges WHERE "
                                "photo_id_a = ? OR photo_id_b = ?",
                                (r["id"], r["id"]))
                            wconn.execute(
                                "INSERT OR IGNORE INTO purged VALUES "
                                "(?, datetime('now'))", (r["sha256"],))
                            wconn.execute(
                                "DELETE FROM photos WHERE id = ?", (r["id"],))
                            n += 1
                        wconn.commit()
                    finally:
                        wconn.close()
                    self._json({"purged": n, "bin": binv})
            elif url.path == "/api/purged":
                try:
                    rows = conn.execute("SELECT sha256 FROM purged").fetchall()
                    self._json({"shas": [r["sha256"] for r in rows]})
                except sqlite3.OperationalError:
                    self._json({"shas": []})
            elif url.path == "/api/photo/share":
                # email one photo from the family address. LAN-triggered,
                # human-initiated, one photo per call — the recipient box in
                # the UI is the only input.
                pid = int(q["id"][0])
                to = q.get("to", [""])[0].strip()
                if "@" not in to or "." not in to.split("@")[-1]:
                    self._send(400, b'{"error": "bad address"}',
                               "application/json")
                    return
                if not (config.SMTP_USER and config.SMTP_PASS):
                    self._send(503, json.dumps(
                        {"error": "email not configured"}).encode(),
                        "application/json")
                    return
                row = conn.execute(
                    "SELECT sha256, taken_at FROM photos WHERE id = ?",
                    (pid,)).fetchone()
                if not row:
                    self._send(404, b'{"error": "no such photo"}',
                               "application/json")
                    return
                jpg = _display_rendition(conn, row["sha256"][:16])
                if not jpg:
                    self._send(404, b'{"error": "photo file missing"}',
                               "application/json")
                    return
                caption = ""
                try:
                    c = conn.execute(
                        "SELECT caption FROM descriptions WHERE photo_id = ?",
                        (pid,)).fetchone()
                    caption = (c["caption"] or "") if c else ""
                except sqlite3.OperationalError:
                    pass
                import smtplib
                from email.message import EmailMessage

                msg = EmailMessage()
                msg["Subject"] = "A photo shared from our family Memory Vault"
                msg["From"] = config.SHARE_FROM or config.SMTP_USER
                msg["To"] = to
                body = "Sharing a photo from our family collection."
                if row["taken_at"]:
                    body += f"\nTaken: {row['taken_at'][:10]}"
                if caption:
                    body += f"\n{caption}"
                msg.set_content(body)
                msg.add_attachment(jpg.read_bytes(), maintype="image",
                                   subtype="jpeg", filename="photo.jpg")
                try:
                    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT,
                                      timeout=25) as s:
                        s.starttls()
                        s.login(config.SMTP_USER, config.SMTP_PASS)
                        s.send_message(msg)
                except Exception as e:
                    print(f"[brain] share failed: {e!r}", file=sys.stderr,
                          flush=True)
                    self._send(502, json.dumps(
                        {"error": "send failed"}).encode(), "application/json")
                    return
                self._json({"sent": to, "photo": pid})
            elif url.path == "/api/photo/vault":
                # move a photo into the encrypted personal vault — becomes the
                # ONLY copy under our control; scrubs library files + DB rows
                from .. import vault as vaultmod
                pid = int(q["id"][0])
                if not vaultmod.is_mounted():
                    self._send(409, json.dumps(
                        {"error": "vault locked"}).encode(), "application/json")
                    return
                dest = q.get("dest", [""])[0]
                if dest not in ("partner", "other"):
                    dest = None
                wconn = connect(config.DB_PATH)
                try:
                    vaultmod.route_to_vault(wconn, pid, review=False, dest=dest)
                    wconn.commit()
                finally:
                    wconn.close()
                self._json({"vaulted": pid, "dest": dest or "vault root"})
            elif url.path == "/api/photo/markdelete":
                # flag for the permanent-delete bin; actual deletion still
                # requires the typed-DELETE purge on /curation
                pid = int(q["id"][0])
                wconn = connect(config.DB_PATH)
                try:
                    wconn.execute(
                        "DELETE FROM tags WHERE photo_id = ? AND dimension IN "
                        "('curation', 'curation_reason')", (pid,))
                    wconn.execute(
                        "INSERT OR IGNORE INTO tags (photo_id, dimension, "
                        "value, confidence, model_version) VALUES "
                        "(?, 'curation', 'Delete', 1.0, 'user-1.0')", (pid,))
                    wconn.execute(
                        "INSERT OR IGNORE INTO tags (photo_id, dimension, "
                        "value, confidence, model_version) VALUES "
                        "(?, 'curation_reason', 'user-marked-delete', 1.0, "
                        "'user-1.0')", (pid,))
                    wconn.commit()
                finally:
                    wconn.close()
                self._json({"marked": pid})
            elif url.path == "/api/vaulted":
                try:
                    rows = conn.execute("SELECT sha256 FROM vaulted").fetchall()
                    self._json({"shas": [r["sha256"] for r in rows]})
                except sqlite3.OperationalError:
                    self._json({"shas": []})
            elif url.path == "/api/photo/remove":
                pid = int(q["id"][0])
                wconn = connect(config.DB_PATH)
                try:
                    wconn.execute(
                        "INSERT OR IGNORE INTO tags (photo_id, dimension, "
                        "value, confidence, model_version) VALUES "
                        "(?, 'curation', 'Removed', 1.0, 'user-1.0')", (pid,))
                    wconn.execute(
                        "INSERT OR IGNORE INTO tags (photo_id, dimension, "
                        "value, confidence, model_version) VALUES "
                        "(?, 'curation_reason', 'user-removed', 1.0, "
                        "'user-1.0')", (pid,))
                    wconn.commit()
                finally:
                    wconn.close()
                self._json({"removed": pid})
            elif url.path == "/api/curation":
                off = int(q.get("offset", [0])[0])
                lim = min(200, int(q.get("limit", [60])[0]))
                binv = q.get("v", ["Trash"])[0]
                if binv not in ("Trash", "Removed", "Delete"):
                    binv = "Trash"
                rows = conn.execute(
                    "SELECT p.id, p.sha256, p.width, p.height, "
                    " (SELECT value FROM tags WHERE photo_id = p.id "
                    "  AND dimension = 'curation_reason') AS reason "
                    "FROM photos p JOIN tags t ON t.photo_id = p.id "
                    "WHERE t.dimension = 'curation' AND t.value = ? "
                    "ORDER BY p.width * p.height DESC LIMIT ? OFFSET ?",
                    (binv, lim, off)).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) c FROM tags WHERE dimension = 'curation' "
                    "AND value = ?", (binv,)).fetchone()["c"]
                self._json({"total": total, "offset": off, "photos": [
                    {"id": r["id"], "thumb": f"/thumb/{r['sha256'][:16]}.jpg",
                     "display": f"/display/{r['sha256'][:16]}.jpg",
                     "dims": f"{r['width']}x{r['height']}",
                     "reason": r["reason"] or "?"} for r in rows]})
            elif url.path == "/api/curation/restore":
                # 'Kept' (not a row delete): a bare restore would boomerang —
                # the nightly heuristics re-examine untagged photos and would
                # re-flag the same signals. Kept is a permanent human override.
                pid = int(q["id"][0])
                wconn = connect(config.DB_PATH)
                try:
                    wconn.execute(
                        "DELETE FROM tags WHERE photo_id = ? AND dimension IN "
                        "('curation', 'curation_reason')", (pid,))
                    wconn.execute(
                        "INSERT OR IGNORE INTO tags (photo_id, dimension, "
                        "value, confidence, model_version) VALUES "
                        "(?, 'curation', 'Kept', 1.0, 'user-1.0')", (pid,))
                    wconn.execute(
                        "INSERT OR IGNORE INTO tags (photo_id, dimension, "
                        "value, confidence, model_version) VALUES "
                        "(?, 'curation_reason', 'user-restored', 1.0, "
                        "'user-1.0')", (pid,))
                    wconn.commit()
                finally:
                    wconn.close()
                self._json({"restored": pid})
            elif url.path == "/progress":
                page = (STATIC_DIR / "progress.html").read_bytes()
                self._send(200, page, "text/html")
            elif url.path == "/api/progress":
                self._json(self.braindb.progress(conn))
            elif url.path == "/api/categories":
                top = max(6, min(80, int(q.get("top", [40])[0])))
                self._json(self.braindb.categories(conn, top=top))
            elif url.path == "/api/category":
                self._json(self.braindb.category_photos(
                    conn, q["dim"][0], q["value"][0],
                    limit=min(200, int(q.get("limit", [48])[0])),
                    offset=int(q.get("offset", [0])[0])))
            elif url.path == "/api/catphoto":
                # one random presentable photo for a node face; family=1
                # draws from the whole library
                if q.get("family"):
                    row = conn.execute(
                        "SELECT id, sha256 FROM photos WHERE status IN "
                        "('screened','tagged','noted') " + self.braindb._NOT_TRASH +
                        "ORDER BY RANDOM() LIMIT 1").fetchone()
                else:
                    row = conn.execute(
                        "SELECT p.id, p.sha256 FROM photos p JOIN tags t "
                        "ON t.photo_id = p.id WHERE t.dimension = ? "
                        "AND t.value = ? AND p.status IN "
                        "('screened','tagged','noted') AND p.id NOT IN "
                        "(SELECT photo_id FROM tags WHERE dimension='curation' "
                        " AND value IN ('Trash','Removed','Delete')) "
                        "ORDER BY RANDOM() LIMIT 1",
                        (q["dim"][0], q["value"][0])).fetchone()
                self._json({"id": row["id"] if row else None,
                            "thumb": f"/thumb/{row['sha256'][:16]}.jpg"
                                     if row else None})
            elif url.path == "/api/family":
                total = conn.execute(
                    "SELECT COUNT(*) c FROM photos WHERE status IN "
                    "('screened','tagged','noted') AND id NOT IN "
                    "(SELECT photo_id FROM tags WHERE dimension='curation' "
                    " AND value='Trash')").fetchone()["c"]
                span = conn.execute(
                    "SELECT MIN(substr(taken_at,1,4)) a, "
                    "MAX(substr(taken_at,1,4)) b FROM photos "
                    "WHERE taken_at IS NOT NULL").fetchone()
                people = conn.execute(
                    "SELECT value, COUNT(*) c FROM tags "
                    "WHERE dimension='people' AND value NOT IN "
                    "('None','Unknown') GROUP BY value ORDER BY c DESC "
                    "LIMIT 12").fetchall()
                recent = conn.execute(
                    "SELECT * FROM photos WHERE status IN "
                    "('screened','tagged','noted') AND taken_at IS NOT NULL "
                    "AND id NOT IN (SELECT photo_id FROM tags WHERE "
                    "dimension='curation' AND value='Trash') "
                    "ORDER BY taken_at DESC LIMIT 24").fetchall()
                self._json({
                    "total": total,
                    "from": span["a"], "to": span["b"],
                    "people": [{"name": r["value"], "count": r["c"]}
                               for r in people],
                    "photos": [_node(r) for r in recent]})
            elif url.path == "/api/intersect":
                rows = conn.execute(
                    "SELECT p.* FROM photos p "
                    "JOIN tags t1 ON t1.photo_id = p.id AND t1.dimension = ? "
                    " AND t1.value = ? "
                    "JOIN tags t2 ON t2.photo_id = p.id AND t2.dimension = ? "
                    " AND t2.value = ? "
                    "ORDER BY p.taken_at IS NULL, p.taken_at DESC LIMIT 60",
                    (q["d1"][0], q["v1"][0], q["d2"][0], q["v2"][0])).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) c FROM photos p "
                    "JOIN tags t1 ON t1.photo_id = p.id AND t1.dimension = ? "
                    " AND t1.value = ? "
                    "JOIN tags t2 ON t2.photo_id = p.id AND t2.dimension = ? "
                    " AND t2.value = ?",
                    (q["d1"][0], q["v1"][0], q["d2"][0], q["v2"][0])
                ).fetchone()["c"]
                self._json({"count": total, "photos": [_node(r) for r in rows]})
            elif url.path == "/api/start":
                self._json({"id": self.braindb.start_node(conn)})
            elif url.path == "/api/neighborhood":
                pid = int(q["id"][0])
                k = int(q.get("k", [DEFAULT_K])[0])
                self._json(self.braindb.neighborhood(conn, pid, k))
            elif url.path == "/api/photo":
                self._json(self.braindb.photo_detail(conn, int(q["id"][0])))
            elif url.path.startswith("/thumb/"):
                f = config.LIBRARY_ROOT / "thumbnails" / Path(url.path).name
                if f.exists():
                    self._send(200, f.read_bytes(), "image/jpeg")
                else:
                    self._send(404, b"not found", "text/plain")
            elif url.path.startswith("/face/"):
                fid = int(Path(url.path).stem)
                row = conn.execute(
                    "SELECT f.bbox, p.sha256, p.width, p.height, "
                    "p.library_path FROM faces f JOIN photos p "
                    "ON p.id = f.photo_id WHERE f.id = ?", (fid,)).fetchone()
                if not row:
                    self._send(404, b"not found", "text/plain")
                else:
                    out = config.LIBRARY_ROOT / "face-crops" / f"{fid}.jpg"
                    if not out.exists():
                        from PIL import Image, ImageOps

                        out.parent.mkdir(parents=True, exist_ok=True)
                        x1, y1, x2, y2 = json.loads(row["bbox"])
                        src = config.LIBRARY_ROOT / row["library_path"]
                        with Image.open(src) as im:
                            im = ImageOps.exif_transpose(im).convert("RGB")
                            # bbox is in detection space (long side <= 1600)
                            sc = max(im.width, im.height) / min(
                                1600, max(im.width, im.height))
                            cx, cy = (x1 + x2) / 2 * sc, (y1 + y2) / 2 * sc
                            half = max(x2 - x1, y2 - y1) * sc * 0.95
                            box = (max(0, int(cx - half)), max(0, int(cy - half)),
                                   min(im.width, int(cx + half)),
                                   min(im.height, int(cy + half)))
                            im.crop(box).resize((220, 220)).save(
                                out, "JPEG", quality=88)
                    self._send(200, out.read_bytes(), "image/jpeg")
            elif url.path.startswith("/display/"):
                sha16 = Path(url.path).stem
                f = _display_rendition(conn, sha16) if sha16.isalnum() else None
                if f:
                    self._send(200, f.read_bytes(), "image/jpeg")
                else:
                    self._send(404, b"not found", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:
            print(f"[brain] 500 {url.path}: {e}", file=sys.stderr, flush=True)
            traceback.print_exc()
            self._send(500, b'{"error": "internal"}', "application/json")
        finally:
            conn.close()


def serve(host: str = "0.0.0.0", port: int = 8484, db_path: Path | None = None):
    Handler.braindb = BrainDB(db_path or config.DB_PATH)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"The Brain: http://{host}:{port}/  (ambient mode: /ambient)")
    httpd.serve_forever()
