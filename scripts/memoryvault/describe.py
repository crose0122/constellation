"""Caption + OCR + orientation pass (one GPU call per photo).

Adds a natural-language layer the tag dimensions can't carry:
  caption      — one specific sentence -> free-text gallery search
  ocr_text     — verbatim text visible in the photo (banners, jerseys,
                 signs) -> searchable, great for dating events
  orientation  — how the image must be rotated to look upright; applied to
                 thumbnails and display renditions ONLY. Originals are never
                 rewritten: the library's identity system is keyed on the
                 file's sha256, so mutating bytes would orphan the photo.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config
from .db import record_error, start_run, finish_run

MODEL_VERSION = "describe-1.0"

PROMPT = (
    "You are archiving a family photo. Answer three things.\n"
    '"caption": ONE specific sentence describing what is actually happening — '
    "the people (never guess names — say 'a boy', 'a woman'), the setting, "
    "and the activity or mood. Concrete details beat generic phrasing.\n"
    '"text_in_image": transcribe any readable text in the photo verbatim '
    "(banners, signs, shirts, screens, handwriting). Empty string if none.\n"
    '"orientation": how must this image be rotated so it appears upright? '
    "upright if it already looks correct. Judge by faces, horizons, and "
    "gravity (hanging objects, standing people)."
)

FORMAT = {
    "type": "object",
    "properties": {
        "caption": {"type": "string"},
        "text_in_image": {"type": "string"},
        "orientation": {
            "type": "string",
            "enum": ["upright", "rotate_90_clockwise_to_fix",
                     "rotate_90_counterclockwise_to_fix", "rotate_180_to_fix"],
        },
    },
    "required": ["caption", "text_in_image", "orientation"],
}

VIDEO_PROMPT = (
    "These images are frames sampled IN ORDER from a single short home video. "
    "Treat them as one clip, not separate photos.\n"
    '"caption": ONE sentence describing what happens across the video — the '
    "people (never guess names — 'a boy', 'a woman'), the setting, and any "
    "action or change over the clip. Concrete beats generic.\n"
    '"text_in_image": any readable text seen in any frame, verbatim; empty '
    "string if none."
)

VIDEO_FORMAT = {
    "type": "object",
    "properties": {"caption": {"type": "string"},
                   "text_in_image": {"type": "string"}},
    "required": ["caption", "text_in_image"],
}


def caption_video(frames) -> dict:
    """One qwen call over several ordered frames → a caption that spans the
    clip (cheap pseudo-temporal understanding). Same model, more images."""
    import requests

    from .tag import model_image_b64
    imgs = [model_image_b64(str(f)) for f in frames]
    from .vision_http import post_vision_text
    text = post_vision_text({
        "model": config.VISION_MODEL, "prompt": VIDEO_PROMPT,
        "images": imgs, "stream": False, "format": VIDEO_FORMAT},
        timeout=180)
    if "```" in text:
        text = (text.split("```json")[-1].split("```")[0]
                if "```json" in text else text.split("```")[1])
    return json.loads(text.strip())


_TRANSPOSE = {
    "rotate_90_clockwise_to_fix": "ROTATE_270",   # PIL rotates CCW
    "rotate_90_counterclockwise_to_fix": "ROTATE_90",
    "rotate_180_to_fix": "ROTATE_180",
}


def apply_orientation(im, orientation: str | None):
    """Rotate a PIL image per a stored orientation verdict (no-op if upright)."""
    from PIL import Image

    op = _TRANSPOSE.get(orientation or "")
    return im.transpose(getattr(Image.Transpose, op)) if op else im


def _refresh_renditions(row, orientation: str):
    """A rotated verdict invalidates the derived images: rebuild the thumb
    with the fix applied and drop the cached display rendition (the server
    rebuilds it on demand, consulting the descriptions table)."""
    from PIL import Image, ImageOps

    from .ingest import make_thumbnail

    src = config.LIBRARY_ROOT / row["library_path"]
    if not src.exists():
        return
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        im = apply_orientation(im, orientation)
        make_thumbnail(im, row["sha256"])
    (config.LIBRARY_ROOT / "display" /
     f"{row['sha256'][:16]}.jpg").unlink(missing_ok=True)


def describe(conn, shard: str | None = None, limit: int | None = None) -> dict:
    from .tag import call_vision

    conn.execute(
        "CREATE TABLE IF NOT EXISTS descriptions ("
        "photo_id INTEGER PRIMARY KEY, caption TEXT, ocr_text TEXT, "
        "orientation TEXT, model_version TEXT, "
        "created_at TEXT DEFAULT (datetime('now')))")
    conn.commit()
    run = start_run(conn, "describe")

    sql = (
        "SELECT id, sha256, media_kind, library_path FROM photos "
        "WHERE status IN ('screened','tagged','noted') "
        "AND library_path IS NOT NULL "
        "AND id NOT IN (SELECT photo_id FROM descriptions) "
        "AND id NOT IN (SELECT photo_id FROM tags WHERE dimension = 'curation' "
        " AND value IN ('Trash','Removed','Delete')) "
    )
    if shard:
        i, m = (int(x) for x in shard.split("/"))
        sql += f"AND id % {m} = {i} "
    sql += "ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    stats = {"described": 0, "with_text": 0, "rotated": 0, "errors": 0}

    from . import video as vid
    from .video import representative_image

    for i, row in enumerate(rows, 1):
        try:
            is_video = row["media_kind"] == "video"
            if is_video:
                # multi-frame: caption what happens ACROSS the clip, one call
                frames = vid.caption_frames(
                    str(config.LIBRARY_ROOT / row["library_path"]),
                    row["sha256"], row["duration"] if "duration" in row.keys() else None)
                if not frames:
                    frames = [Path(representative_image(row))]
                try:
                    raw = caption_video(frames)
                finally:
                    vid.cleanup_frames(frames)
                orientation = "upright"   # never re-orient a video
            else:
                raw = call_vision(representative_image(row), PROMPT, FORMAT)
                orientation = raw.get("orientation", "upright")
                if orientation not in FORMAT["properties"]["orientation"]["enum"]:
                    orientation = "upright"
            caption = str(raw.get("caption", "")).strip()
            ocr = str(raw.get("text_in_image", "")).strip()
            conn.execute(
                "INSERT OR REPLACE INTO descriptions "
                "(photo_id, caption, ocr_text, orientation, model_version) "
                "VALUES (?,?,?,?,?)",
                (row["id"], caption, ocr, orientation, MODEL_VERSION))
            if orientation != "upright":
                _refresh_renditions(row, orientation)
                stats["rotated"] += 1
            if ocr:
                stats["with_text"] += 1
            stats["described"] += 1
        except Exception as e:
            stats["errors"] += 1
            record_error(conn, "describe", repr(e), photo_id=row["id"])
        conn.commit()
        if i % 200 == 0:
            print(f"  {i}/{len(rows)} described, {stats['with_text']} with "
                  f"text, {stats['rotated']} rotated", flush=True)

    finish_run(conn, run, stats)
    return stats
