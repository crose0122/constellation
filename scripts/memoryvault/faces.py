"""Face recognition (SPEC v1.4): detect → embed → cluster → human labels.

InsightFace buffalo_l (ArcFace embeddings) on CPU — the same approach Immich
uses. Faces cluster by cosine similarity; a human names a cluster once on the
/people page and the name is written as a `people` tag to every photo in it,
which makes the person searchable in the gallery. Vault content never gets
here by construction (it isn't in photos.db).

CLI:  mvault faces scan      (detect+embed new photos; resumable)
      mvault faces cluster   (re-cluster everything; labels survive)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import config
from .db import record_error

SIM_THRESHOLD = 0.45   # cosine similarity to join an existing cluster
MIN_QUALITY = 0.55     # detector score floor — skip blurry half-faces
MIN_SIZE_PX = 40       # tiny faces embed badly


def ensure_schema(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS faces ("
        " id INTEGER PRIMARY KEY,"
        " photo_id INTEGER NOT NULL REFERENCES photos(id),"
        " bbox TEXT NOT NULL,"
        " quality REAL,"
        " embedding BLOB NOT NULL,"
        " cluster_id INTEGER)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS face_clusters ("
        " id INTEGER PRIMARY KEY,"
        " label TEXT,"
        " centroid BLOB)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_faces_photo ON faces(photo_id)")
    # /api/people's per-cluster best-face lookup is 19s -> 14ms with this
    conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_cluster "
                 "ON faces(cluster_id, quality DESC)")
    # human "not them" verdicts — survive reclustering forever
    conn.execute("CREATE TABLE IF NOT EXISTS face_bans ("
                 " face_id INTEGER NOT NULL,"
                 " label TEXT NOT NULL,"
                 " PRIMARY KEY (face_id, label))")
    try:
        conn.execute("ALTER TABLE photos ADD COLUMN faces_scanned INTEGER "
                     "NOT NULL DEFAULT 0")
    except Exception:
        pass
    conn.commit()


_app = None


def _analyzer():
    global _app
    if _app is None:
        from insightface.app import FaceAnalysis

        _app = FaceAnalysis(name="buffalo_l",
                            providers=["CPUExecutionProvider"])
        _app.prepare(ctx_id=-1, det_size=(640, 640))
    return _app


def scan(conn, limit: int | None = None) -> dict:
    """Detect + embed faces for photos not yet scanned. Resumable."""
    import cv2

    ensure_schema(conn)
    sql = (
        "SELECT id, sha256, media_kind, library_path FROM photos "
        "WHERE status IN ('screened','tagged','noted') AND faces_scanned = 0 "
        "AND library_path IS NOT NULL AND id NOT IN "
        " (SELECT photo_id FROM tags WHERE dimension = 'curation' "
        "  AND value = 'Trash') ORDER BY id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    app = _analyzer()
    from .video import representative_image

    stats = {"scanned": 0, "faces": 0, "errors": 0}
    for i, row in enumerate(rows, 1):
        path = Path(representative_image(row))   # poster frame for videos
        try:
            img = cv2.imread(str(path))
            if img is None:
                # cv2 can't decode HEIC (and some odd files) — fall back to
                # PIL, which has pillow-heif registered; this gap left ~1,700
                # iPhone photos out of every cluster
                try:
                    import pillow_heif

                    pillow_heif.register_heif_opener()
                except ImportError:
                    pass
                from PIL import Image, ImageOps

                with Image.open(path) as pim:
                    pim = ImageOps.exif_transpose(pim).convert("RGB")
                    img = cv2.cvtColor(np.array(pim), cv2.COLOR_RGB2BGR)
            h, w = img.shape[:2]
            if max(h, w) > 1600:  # detector doesn't need full res
                s = 1600 / max(h, w)
                img = cv2.resize(img, (int(w * s), int(h * s)))
            for f in app.get(img):
                x1, y1, x2, y2 = (int(v) for v in f.bbox)
                if f.det_score < MIN_QUALITY or (x2 - x1) < MIN_SIZE_PX:
                    continue
                emb = f.normed_embedding.astype(np.float32)
                conn.execute(
                    "INSERT INTO faces (photo_id, bbox, quality, embedding) "
                    "VALUES (?, ?, ?, ?)",
                    (row["id"], json.dumps([x1, y1, x2, y2]),
                     float(f.det_score), emb.tobytes()))
                stats["faces"] += 1
            conn.execute("UPDATE photos SET faces_scanned = 1 WHERE id = ?",
                         (row["id"],))
            stats["scanned"] += 1
        except Exception as e:
            stats["errors"] += 1
            record_error(conn, "faces", repr(e), photo_id=row["id"])
            conn.execute("UPDATE photos SET faces_scanned = 1 WHERE id = ?",
                         (row["id"],))
        conn.commit()
        if i % 200 == 0:
            print(f"  scanned {i}/{len(rows)} ({stats['faces']} faces)",
                  flush=True)
    return stats


def cluster(conn) -> dict:
    """Incremental centroid clustering. Labels attach to clusters and are
    re-applied as `people` tags after every re-cluster, so naming survives."""
    ensure_schema(conn)
    faces = conn.execute(
        "SELECT id, embedding FROM faces ORDER BY quality DESC").fetchall()
    old_labels = {
        r["id"]: r["label"] for r in
        conn.execute("SELECT id, label FROM face_clusters "
                     "WHERE label IS NOT NULL")
    }
    # remember which old cluster each face was in, to migrate labels
    face_old = {
        r["id"]: r["cluster_id"] for r in
        conn.execute("SELECT id, cluster_id FROM faces "
                     "WHERE cluster_id IS NOT NULL")
    }
    centroids: list[np.ndarray] = []
    members: list[list[int]] = []
    for f in faces:
        emb = np.frombuffer(f["embedding"], dtype=np.float32)
        best, best_sim = -1, SIM_THRESHOLD
        for ci, c in enumerate(centroids):
            sim = float(np.dot(emb, c) / (np.linalg.norm(c) or 1))
            if sim > best_sim:
                best, best_sim = ci, sim
        if best < 0:
            centroids.append(emb.copy())
            members.append([f["id"]])
        else:
            n = len(members[best])
            centroids[best] = (centroids[best] * n + emb) / (n + 1)
            members[best].append(f["id"])

    conn.execute("DELETE FROM face_clusters")
    labeled = 0
    for ci, ids in enumerate(members, 1):
        # migrate a label if most members came from one labeled old cluster
        votes: dict[str, int] = {}
        for fid in ids:
            lbl = old_labels.get(face_old.get(fid))
            if lbl:
                votes[lbl] = votes.get(lbl, 0) + 1
        label = max(votes, key=votes.get) if votes else None
        if label:
            labeled += 1
        conn.execute(
            "INSERT INTO face_clusters (id, label, centroid) VALUES (?, ?, ?)",
            (ci, label, centroids[ci - 1].astype(np.float32).tobytes()))
        conn.executemany(
            "UPDATE faces SET cluster_id = ? WHERE id = ?",
            [(ci, fid) for fid in ids])
    conn.commit()
    _enforce_bans(conn)
    _apply_labels(conn)
    return {"faces": len(faces), "clusters": len(members),
            "labeled": labeled}


def label_cluster(conn, cluster_id: int, label: str) -> dict:
    ensure_schema(conn)
    conn.execute("UPDATE face_clusters SET label = ? WHERE id = ?",
                 (label.strip() or None, cluster_id))
    conn.commit()
    _enforce_bans(conn)
    _apply_labels(conn)
    return {"cluster": cluster_id, "label": label.strip()}


def ban_face(conn, face_id: int) -> dict:
    """Human verdict: this face is NOT the person its cluster is labeled as.
    Detaches the face and records a permanent ban so no future recluster can
    ever tag that name onto this face's photo again. Touches ONLY this
    photo's tag — a full _apply_labels rebuild here took tens of seconds
    under sweep contention and phones gave up waiting on the response."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT f.photo_id, c.label FROM faces f JOIN face_clusters c "
        "ON c.id = f.cluster_id WHERE f.id = ?", (face_id,)).fetchone()
    label = row["label"] if row and row["label"] else None
    if label:
        conn.execute("INSERT OR IGNORE INTO face_bans VALUES (?, ?)",
                     (face_id, label))
    conn.execute("UPDATE faces SET cluster_id = NULL WHERE id = ?",
                 (face_id,))
    if label:
        still_there = conn.execute(
            "SELECT 1 FROM faces f JOIN face_clusters c "
            "ON c.id = f.cluster_id WHERE f.photo_id = ? AND c.label = ? "
            "LIMIT 1", (row["photo_id"], label)).fetchone()
        if not still_there:
            conn.execute(
                "DELETE FROM tags WHERE photo_id = ? AND dimension = 'people' "
                "AND value = ? AND model_version = 'faces-1.0'",
                (row["photo_id"], label))
    conn.commit()
    return {"face": face_id, "banned_from": label}


def _enforce_bans(conn):
    """A face the human rejected from a name must never re-join it."""
    conn.execute(
        "UPDATE faces SET cluster_id = NULL WHERE id IN ("
        " SELECT f.id FROM faces f "
        " JOIN face_clusters c ON c.id = f.cluster_id "
        " JOIN face_bans b ON b.face_id = f.id AND b.label = c.label)")
    conn.commit()


def _apply_labels(conn):
    """Write each labeled cluster's name as a people tag on its photos."""
    conn.execute("DELETE FROM tags WHERE dimension = 'people' "
                 "AND model_version = 'faces-1.0'")
    conn.execute(
        "INSERT OR IGNORE INTO tags "
        " (photo_id, dimension, value, confidence, model_version) "
        "SELECT DISTINCT f.photo_id, 'people', c.label, 0.95, 'faces-1.0' "
        "FROM faces f JOIN face_clusters c ON c.id = f.cluster_id "
        "WHERE c.label IS NOT NULL AND TRIM(c.label) != ''")
    conn.commit()
