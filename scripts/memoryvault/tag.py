"""Stage 5 — Vision tagging. Only SAFE (screened) photos
reach this stage. Tags land in the DB with a model_version stamp."""

import base64
import json
import sqlite3
import time
from pathlib import Path

from . import config
from .db import record_error, start_run, finish_run


def load_schema() -> dict:
    with open(config.TAG_SCHEMA_PATH) as f:
        return json.load(f)


def model_version(schema: dict) -> str:
    return f"{config.VISION_MODEL}@schema-{schema.get('version', '?')}"


def vision_dims(schema: dict) -> dict:
    """Dimensions the vision model is asked about. people (face recognition
    is authoritative) and year (EXIF is authoritative) carry vision: false."""
    return {k: d for k, d in schema["dimensions"].items()
            if d.get("vision", True)}


def build_prompt(schema: dict) -> str:
    lines = [
        "You are tagging a family photo for an archive. Answer every field "
        "from the allowed values only. Base every answer on what is actually "
        "visible — when unsure, use the field's Unknown/None/default value "
        "rather than guessing.",
        "",
    ]
    for key, dim in vision_dims(schema).items():
        lines.append(f'"{key}": {dim["prompt"]}')
    return "\n".join(lines)


def build_format(schema: dict) -> dict:
    """JSON-schema for Ollama structured output: every vision dimension is
    required and enum-constrained, so invented values ('With New Pet',
    'With Bailey', comma-joined combos) are grammatically impossible."""
    props = {}
    for key, dim in vision_dims(schema).items():
        if dim["type"] == "multi":
            props[key] = {
                "type": "array",
                "items": {"type": "string", "enum": dim["values"]},
                "maxItems": dim.get("max", 4),
            }
        else:
            props[key] = {"type": "string", "enum": dim["values"]}
    return {"type": "object", "properties": props,
            "required": list(props)}


def model_image_b64(path: str, max_px: int = 1024) -> str:
    """Downscaled JPEG for the model call. Raw 12MP files 400 on Ollama, and
    HEIC bytes aren't decodable server-side — re-encoding fixes both; tags
    don't need full resolution."""
    import io

    from PIL import Image, ImageOps

    with Image.open(path) as im:
        # Decode at reduced scale where the format allows it. Without this the
        # 108MP phone shots in this library are fully decoded — about 324MB of
        # RGB — purely to be thrown away by the thumbnail on the next line. On
        # a box running two shards that is enough to push it into swap, and a
        # stage swapping on a huge decode looks exactly like a hang: no CPU, no
        # progress, no error. draft() is a no-op for formats that cannot do it.
        im.draft("RGB", (max_px, max_px))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def call_vision(path: str, prompt: str, fmt: dict | str = "json") -> dict:
    from .vision_http import post_vision_text

    image_b64 = model_image_b64(path)
    text = post_vision_text({
        "model": config.VISION_MODEL,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
        "format": fmt,
    }, timeout=120)
    if "```" in text:  # tolerate markdown fencing
        text = text.split("```json")[-1].split("```")[0] if "```json" in text else \
            text.split("```")[1]
    return json.loads(text.strip())


def store_tags(conn, photo_id: int, raw: dict, schema: dict, taken_at: str | None,
               replace: bool = False):
    mv = model_version(schema)
    if replace:
        # drop this photo's previous VISION tags only — the '@schema-' stamp
        # is unique to this stage, so faces-1.0, geocode-1.0, rollup-1.0 and
        # curation rows survive a re-tag untouched
        conn.execute(
            "DELETE FROM tags WHERE photo_id = ? AND model_version LIKE '%@schema-%' "
            "AND source = 'machine'",
            (photo_id,),
        )
    dims = vision_dims(schema)
    for key, dim in dims.items():
        if key not in raw or not raw[key]:
            continue
        if has_human_tags(conn, photo_id, key):
            # curator already corrected this dimension — leave it alone
            continue
        values = raw[key] if isinstance(raw[key], list) else [raw[key]]
        canon = {v.lower(): v for v in dim["values"]}
        for v in values:
            # split lingering comma-combos, then canonicalize against the
            # schema — anything not in the enum is dropped, not stored
            for part in str(v).split(","):
                part = canon.get(part.strip().lower())
                if not part or part in ("None", "Unknown"):
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO tags(photo_id, dimension, value, model_version) "
                    "VALUES (?,?,?,?)",
                    (photo_id, key, part, mv),
                )
    if taken_at:  # EXIF is authoritative for year (SPEC §5.5)
        conn.execute(
            "INSERT OR REPLACE INTO tags(photo_id, dimension, value, model_version) "
            "VALUES (?, 'year', ?, ?)",
            (photo_id, taken_at[:4], mv),
        )


def tag(conn, vision_fn=call_vision, limit: int | None = None,
        shard: str | None = None, retag: bool = False) -> dict:
    if not config.VISION_ENABLED:
        return {"skipped": "tag", "reason": "vision model is off on this install"}
    schema = load_schema()
    prompt = build_prompt(schema)
    fmt = build_format(schema)
    run = start_run(conn, "tag")

    # curated Trash (Minecraft textures, icons, screenshots) never earns a
    # model call — 6k junk files would cost ~7 GPU-hours for nothing
    if retag:
        # re-tag everything not yet stamped with the CURRENT model_version —
        # resumable: photos done under this schema version are skipped
        sql = (
            "SELECT * FROM photos WHERE status IN ('screened','tagged','noted') "
            "AND id NOT IN (SELECT photo_id FROM tags WHERE model_version = "
            f"'{model_version(schema)}') "
            "AND id NOT IN (SELECT photo_id FROM tags WHERE dimension = 'curation' "
            " AND value = 'Trash') "
        )
    else:
        sql = (
            "SELECT * FROM photos WHERE status = 'screened' AND id NOT IN "
            "(SELECT photo_id FROM tags WHERE dimension = 'curation' "
            " AND value = 'Trash') "
        )
    # shard "i/m": disjoint id-space split so several workers (each pointed
    # at a different inference host) drain the queue without racing
    if shard:
        i, m = (int(x) for x in shard.split("/"))
        sql += f"AND id % {m} = {i} "
    sql += "ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    stats = {"tagged": 0, "errors": 0}

    def _retry_locked(fn, attempts=6, wait=10):
        # an ingest batch can hold the writer lock past busy_timeout; a tag
        # worker should out-wait it, never die on it
        for a in range(attempts):
            try:
                return fn()
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) or a == attempts - 1:
                    raise
                time.sleep(wait)

    from .video import representative_image

    for i, row in enumerate(rows, 1):
        path = representative_image(row)   # poster frame for videos
        try:
            raw = vision_fn(str(path), prompt, fmt)

            def _write():
                store_tags(conn, row["id"], raw, schema, row["taken_at"],
                           replace=retag)
                conn.execute(
                    "UPDATE photos SET status = 'tagged' WHERE id = ? "
                    "AND status = 'screened'",
                    (row["id"],),
                )

            _retry_locked(_write)
            stats["tagged"] += 1
        except Exception as e:
            stats["errors"] += 1
            try:
                _retry_locked(
                    lambda: record_error(conn, "tag", repr(e), photo_id=row["id"]))
            except sqlite3.OperationalError:
                print(f"  UNRECORDED error for photo {row['id']}: {e!r}")
        _retry_locked(conn.commit)
        if i % 25 == 0:
            print(f"  tagged {i}/{len(rows)}")

    finish_run(conn, run, stats)
    return stats


# ---------------------------------------------------------------------------
# Task #379: human (curator) tags. Machine re-tags never overwrite these.
# ---------------------------------------------------------------------------

HUMAN_MODEL_VERSION = "human@manual-1"


def has_human_tags(conn, photo_id: int, dimension: str | None = None) -> bool:
    """Whether a person has already corrected the photo (optionally for one
    dimension). Machine code consults this to stand down."""
    if dimension is None:
        cur = conn.execute(
            "SELECT 1 FROM tags WHERE photo_id = ? AND source = 'human' LIMIT 1",
            (photo_id,),
        )
    else:
        cur = conn.execute(
            "SELECT 1 FROM tags WHERE photo_id = ? AND dimension = ? "
            "AND source = 'human' LIMIT 1",
            (photo_id, dimension),
        )
    return cur.fetchone() is not None


def store_human_tags(conn, photo_id: int, tags: dict, *,
                     author: str | None = None, replace: bool = True,
                     method: str = "click-to-tag") -> list[tuple[str, str]]:
    """Persist curator corrections.

    *tags* is a mapping ``{dimension: value_or_list}``. Every written row
    carries ``source='human'`` and ``model_version='human@manual-1'``, so a
    subsequent machine re-tag (store_tags) leaves it untouched. Returns the
    (dimension, value) pairs actually stored.
    """
    mv = f"{HUMAN_MODEL_VERSION}"
    # opportunistic author tag encoded into model_version keeps the schema
    # (TEXT free-form) and is visible in provenance queries.
    if replace:
        if isinstance(tags, dict) and tags:
            dims = tuple(str(d) for d in tags)
            placeholders = ",".join("?" for _ in dims)
            conn.execute(
                f"DELETE FROM tags WHERE photo_id = ? AND source = 'human' "
                f"AND dimension IN ({placeholders})",
                (photo_id, *dims),
            )
    stored: list[tuple[str, str]] = []
    if not isinstance(tags, dict):
        return stored
    for key, raw in tags.items():
        dim = str(key).strip()
        if not dim:
            continue
        vals = raw if isinstance(raw, (list, tuple)) else [raw]
        for v in vals:
            value = str(v).strip()
            if not value:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO tags"
                "(photo_id, dimension, value, confidence, model_version, source) "
                "VALUES (?,?,?,?,?,?)",
                (photo_id, dim, value, 1.0, mv, "human"),
            )
            stored.append((dim, value))
    conn.commit()
    return stored


def human_tag_manifest(conn, photo_id: int | None = None) -> list[dict]:
    """JSON-serialisable manifest of every human correction in the library
    (or one photo). Intended for the click-to-tag gallery and for the
    audit ledger declared in task #379."""
    sql = (
        "SELECT photo_id, dimension, value, model_version, source "
        "FROM tags WHERE source = 'human' "
    )
    params: tuple = ()
    if photo_id is not None:
        sql += "AND photo_id = ? "
        params = (photo_id,)
    sql += "ORDER BY photo_id, dimension, value"
    rows = conn.execute(sql, params).fetchall()
    return [
        {"photo_id": r[0], "dimension": r[1], "value": r[2],
         "model_version": r[3], "source": r[4]}
        for r in rows
    ]
