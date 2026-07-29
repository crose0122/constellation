"""Curation pass: tag photos that are almost certainly not memories.

Heuristics only — no model call, so it runs in seconds over the whole
library. Writes a `curation: Trash` tag (with a reason value alongside)
that the UI treats specially: the Brain shows Trash as a reviewable
category, the Memories stream excludes it, and NOTHING is deleted —
per the project invariant, deletion always requires a human.

Signals (any one is enough):
  tiny        — shortest side < 200px (icons, avatars, web cache)
  screenshot  — no camera EXIF and pixel-exact common screen dimensions
  extreme     — aspect ratio beyond 4:1 (banners, sprite strips)
  cachepath   — the file only ever appeared under cache/temp/thumbnail dirs
"""
from __future__ import annotations

import re

from . import db as dbm
from . import config

MODEL_VERSION = "curate-heuristic-1.0"
MIN_PX = 200
EXTREME_ASPECT = 4.0

_SCREEN_DIMS = {
    (1920, 1080), (1080, 1920), (1366, 768), (768, 1366), (2560, 1440),
    (1440, 2560), (1280, 720), (720, 1280), (1536, 2048), (2048, 1536),
    (1170, 2532), (1080, 2400), (1440, 3120), (1080, 2340), (750, 1334),
    (828, 1792), (1125, 2436), (640, 960), (600, 800), (800, 600),
}
_CACHE_RE = re.compile(
    r"/(cache|\.cache|thumbnails?|\.thumbnails|temp|tmp|appdata|"
    r"\.stversions|node_modules|site-packages)/", re.IGNORECASE)


def curate(conn) -> dict:
    tagged = 0
    rows = conn.execute(
        "SELECT p.id, p.width, p.height, p.camera, "
        "  (SELECT GROUP_CONCAT(f.source_path, '||') FROM files f "
        "   WHERE f.photo_id = p.id) AS paths "
        "FROM photos p WHERE p.id NOT IN "
        "  (SELECT photo_id FROM tags WHERE dimension = 'curation')"
    ).fetchall()
    for r in rows:
        w, h = r["width"] or 0, r["height"] or 0
        reason = None
        if w and h:
            if min(w, h) < MIN_PX:
                reason = "tiny"
            elif max(w, h) / max(1, min(w, h)) > EXTREME_ASPECT:
                reason = "extreme-aspect"
            elif not r["camera"] and (w, h) in _SCREEN_DIMS:
                has_face = conn.execute(
                    "SELECT 1 FROM faces WHERE photo_id = ? LIMIT 1",
                    (r["id"],)).fetchone()
                if not has_face:
                    reason = "screenshot"
        if reason is None and r["paths"]:
            if all(_CACHE_RE.search(p) for p in r["paths"].split("||")):
                reason = "cache-path"
        if reason:
            conn.execute(
                "INSERT OR IGNORE INTO tags "
                "(photo_id, dimension, value, confidence, model_version) "
                "VALUES (?, 'curation', 'Trash', 0.9, ?)",
                (r["id"], MODEL_VERSION),
            )
            conn.execute(
                "INSERT OR IGNORE INTO tags "
                "(photo_id, dimension, value, confidence, model_version) "
                "VALUES (?, 'curation_reason', ?, 0.9, ?)",
                (r["id"], reason, MODEL_VERSION),
            )
            tagged += 1
    conn.commit()
    total = conn.execute(
        "SELECT COUNT(DISTINCT photo_id) c FROM tags "
        "WHERE dimension = 'curation' AND value = 'Trash'"
    ).fetchone()["c"]
    # rollup: any pet value gets the umbrella 'Pets' tag so an aggregate
    # category exists (No Pets stays in the DB but never in the UI)
    conn.execute(
        "INSERT OR IGNORE INTO tags (photo_id, dimension, value, confidence, "
        "model_version) SELECT DISTINCT photo_id, 'pets', 'Pets', 0.9, "
        "'rollup-1.0' FROM tags WHERE dimension = 'pets' "
        "AND value NOT IN ('No Pets', 'None', 'Unknown', 'Pets')")
    conn.commit()
    return {"newly_tagged": tagged, "trash_total": total,
            "examined": len(rows)}


RESCUE_PROMPT = (
    "Is this a real photograph capturing people, pets, places, or a moment "
    "of family life? Answer yes even if it is small, blurry, dark, old, or "
    "low quality — a real memory in poor condition is still a yes. "
    "Answer no ONLY for things that were never photographs of life: app or "
    "game screenshots, user interfaces, documents, receipts, memes, logos, "
    "graphics, textures, icons, or web images. "
    "Answer with exactly one word: yes or no."
)


def live_photos(conn) -> dict:
    """Hide the motion halves of iPhone Live Photos already ingested as videos.
    The paired still is in the library, so these ~3s clips are redundant — mark
    them curation 'Removed' (hidden everywhere, restorable in /curation) rather
    than delete, in case you ever want the live motion."""
    from . import video as vid

    rows = conn.execute(
        "SELECT id, library_path FROM photos WHERE media_kind = 'video' "
        "AND (duration IS NULL OR duration <= 4) AND library_path IS NOT NULL "
        "AND id NOT IN (SELECT photo_id FROM tags WHERE dimension = 'curation')"
    ).fetchall()
    hidden = 0
    for i, r in enumerate(rows, 1):
        if vid.is_live_photo(str(config.LIBRARY_ROOT / r["library_path"])):
            conn.execute(
                "INSERT OR IGNORE INTO tags (photo_id, dimension, value, "
                "confidence, model_version) VALUES "
                "(?, 'curation', 'Removed', 1.0, 'live-photo-1.0')", (r["id"],))
            conn.execute(
                "INSERT OR IGNORE INTO tags (photo_id, dimension, value, "
                "confidence, model_version) VALUES "
                "(?, 'curation_reason', 'live-photo', 1.0, 'live-photo-1.0')",
                (r["id"],))
            hidden += 1
        if i % 200 == 0:
            conn.commit()
            print(f"  {i}/{len(rows)} checked, {hidden} hidden", flush=True)
    conn.commit()
    return {"hidden": hidden, "examined": len(rows)}


def rescue(conn, shard: str | None = None, limit: int | None = None) -> dict:
    """GPU second-opinion over the Trash bin: the heuristics never look at
    pixels, so real photos with stripped EXIF (messenger apps) or small
    dimensions get mis-binned. A yes verdict flips the photo to 'Kept' — a
    permanent override the heuristics respect. Checked photos are stamped
    (curation_check) so nightly re-runs only review NEW trash."""
    from .tag import model_image_b64
    from .vision_http import post_vision_text

    sql = (
        "SELECT p.id, p.library_path FROM photos p "
        "JOIN tags t ON t.photo_id = p.id "
        "WHERE t.dimension = 'curation' AND t.value = 'Trash' "
        "AND p.library_path IS NOT NULL AND p.id NOT IN "
        "(SELECT photo_id FROM tags WHERE dimension = 'curation_check') "
    )
    if shard:
        i, m = (int(x) for x in shard.split("/"))
        sql += f"AND p.id % {m} = {i} "
    sql += "ORDER BY p.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    stats = {"checked": 0, "rescued": 0, "errors": 0}
    for i, row in enumerate(rows, 1):
        try:
            b64 = model_image_b64(
                str(config.LIBRARY_ROOT / row["library_path"]), max_px=640)
            ans = post_vision_text({
                "model": config.VISION_MODEL, "prompt": RESCUE_PROMPT,
                "images": [b64], "stream": False},
                timeout=180).strip().lower()
            if ans.startswith("yes"):
                conn.execute(
                    "UPDATE tags SET value = 'Kept', "
                    "model_version = 'rescue-1.0' WHERE photo_id = ? "
                    "AND dimension = 'curation'", (row["id"],))
                stats["rescued"] += 1
            conn.execute(
                "INSERT OR IGNORE INTO tags (photo_id, dimension, value, "
                "confidence, model_version) VALUES "
                "(?, 'curation_check', 'rescue-reviewed', 0.8, 'rescue-1.0')",
                (row["id"],))
            stats["checked"] += 1
        except Exception as e:
            stats["errors"] += 1
            from .db import record_error
            record_error(conn, "rescue", repr(e), photo_id=row["id"])
        conn.commit()
        if i % 200 == 0:
            print(f"  {i}/{len(rows)} reviewed, {stats['rescued']} rescued",
                  flush=True)
    return stats


DOC_PROMPT = (
    "Is this image junk paperwork or interface content: a document, receipt, "
    "form, app screenshot, settings page, whiteboard, or page of text with "
    "NO people visible? If any person, face, pet, or family moment appears "
    "anywhere in the image, answer no. "
    "Answer with exactly one word: yes or no."
)


def vision_docs(conn, shard: str | None = None, limit: int | None = None) -> dict:
    """GPU pass: bin photographed paperwork/documents as Trash(document).
    Runs after tagging so the GPUs are free; restorable via /curation."""
    from .tag import model_image_b64
    from .vision_http import post_vision_text

    sql = (
        "SELECT id, library_path FROM photos WHERE status IN "
        "('tagged','noted') AND library_path IS NOT NULL AND id NOT IN "
        "(SELECT photo_id FROM tags WHERE dimension = 'curation') "
    )
    if shard:
        i, m = (int(x) for x in shard.split("/"))
        sql += f"AND id % {m} = {i} "
    sql += "ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    stats = {"checked": 0, "documents": 0, "errors": 0}
    for i, row in enumerate(rows, 1):
        try:
            b64 = model_image_b64(
                str(config.LIBRARY_ROOT / row["library_path"]), max_px=640)
            ans = post_vision_text({
                "model": config.VISION_MODEL, "prompt": DOC_PROMPT,
                "images": [b64], "stream": False},
                timeout=180).strip().lower()
            if ans.startswith("yes"):
                conn.execute(
                    "INSERT OR IGNORE INTO tags (photo_id, dimension, value, "
                    "confidence, model_version) VALUES "
                    "(?, 'curation', 'Trash', 0.8, 'docscan-1.0')",
                    (row["id"],))
                conn.execute(
                    "INSERT OR IGNORE INTO tags (photo_id, dimension, value, "
                    "confidence, model_version) VALUES "
                    "(?, 'curation_reason', 'document', 0.8, 'docscan-1.0')",
                    (row["id"],))
                stats["documents"] += 1
            stats["checked"] += 1
        except Exception as e:
            stats["errors"] += 1
            from .db import record_error
            record_error(conn, "docscan", repr(e), photo_id=row["id"])
        conn.commit()
        if i % 200 == 0:
            print(f"  {i}/{len(rows)} checked, {stats['documents']} documents",
                  flush=True)
    return stats


SCREENSHOT_PROMPT = (
    "This image is a screenshot from a phone or computer. Look at what is ON "
    "the screen. "
    "If the screen shows a real photograph — people, pets, a place, food, a "
    "moment someone captured with a camera — answer: photo. "
    "If the screen shows text or interface content — a text-message or chat "
    "conversation, receipt, invoice, order or delivery confirmation, bank or "
    "medical document, article, search results, web page, map, calendar, "
    "game, or an app settings screen — answer: text. "
    "Answer with exactly one word: photo or text."
)


def screenshots(conn, shard: str | None = None, limit: int | None = None) -> dict:
    """GPU pass over screenshot-flagged photos that are still on display.

    `vision_docs` skips anything that already carries a curation tag, so a
    screenshot the heuristic or a human marked Kept was never looked at again
    — which is how text-message captures, receipts and documents kept reaching
    the wall and the Memories stream.

    This pass judges the picture itself: screenshots *of a photograph* are left
    alone, screenshots of text or interface content are re-binned as
    Trash(screenshot-text). Re-binning is reversible from /curation and, per
    the project invariant, nothing here deletes a file.
    """
    from .tag import model_image_b64
    from .vision_http import post_vision_text

    sql = (
        "SELECT p.id, p.library_path FROM photos p "
        "JOIN tags r ON r.photo_id = p.id "
        "  AND r.dimension = 'curation_reason' AND r.value = 'screenshot' "
        "WHERE p.status IN ('tagged','noted','screened') "
        "  AND p.library_path IS NOT NULL "
        # only those still visible: never re-judge something already binned away
        "  AND NOT EXISTS (SELECT 1 FROM tags c WHERE c.photo_id = p.id "
        "                  AND c.dimension = 'curation' "
        "                  AND c.value IN ('Trash','Removed','Delete')) "
    )
    if shard:
        i, m = (int(x) for x in shard.split("/"))
        sql += f"AND p.id % {m} = {i} "
    sql += "ORDER BY p.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()

    stats = {"checked": 0, "binned": 0, "kept": 0, "errors": 0}
    print(f"screenshot triage: {len(rows)} to review", flush=True)
    for i, row in enumerate(rows, 1):
        try:
            b64 = model_image_b64(
                str(config.LIBRARY_ROOT / row["library_path"]), max_px=640)
            ans = post_vision_text({
                "model": config.VISION_MODEL, "prompt": SCREENSHOT_PROMPT,
                "images": [b64], "stream": False},
                timeout=180).strip().lower()
            if ans.startswith("text"):
                # clear whatever bin it was in (a photo may only sit in one)
                conn.execute(
                    "DELETE FROM tags WHERE photo_id = ? AND dimension IN "
                    "('curation','curation_reason')", (row["id"],))
                conn.execute(
                    "INSERT OR IGNORE INTO tags (photo_id, dimension, value, "
                    "confidence, model_version) VALUES "
                    "(?, 'curation', 'Trash', 0.85, 'shotscan-1.0')",
                    (row["id"],))
                conn.execute(
                    "INSERT OR IGNORE INTO tags (photo_id, dimension, value, "
                    "confidence, model_version) VALUES "
                    "(?, 'curation_reason', 'screenshot-text', 0.85, "
                    "'shotscan-1.0')", (row["id"],))
                stats["binned"] += 1
            else:
                stats["kept"] += 1
            stats["checked"] += 1
        except Exception as e:
            stats["errors"] += 1
            from .db import record_error
            record_error(conn, "shotscan", repr(e), photo_id=row["id"])
        conn.commit()
        if i % 100 == 0:
            print(f"  {i}/{len(rows)} checked, {stats['binned']} binned, "
                  f"{stats['kept']} real photos kept", flush=True)
    return stats
