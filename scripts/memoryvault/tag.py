"""Stage 5 — Vision tagging (SPEC.md §5.5). Only SAFE (screened) photos
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
    'With Uncle Joe', comma-joined combos) are grammatically impossible."""
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
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def call_vision(path: str, prompt: str, fmt: dict | str = "json") -> dict:
    import requests

    image_b64 = model_image_b64(path)
    resp = requests.post(
        config.OLLAMA_URL,
        json={
            "model": config.VISION_MODEL,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "format": fmt,
        },
        timeout=120,
    )
    resp.raise_for_status()
    text = resp.json().get("response", "")
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
            "DELETE FROM tags WHERE photo_id = ? AND model_version LIKE '%@schema-%'",
            (photo_id,),
        )
    dims = vision_dims(schema)
    for key, dim in dims.items():
        if key not in raw or not raw[key]:
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

    for i, row in enumerate(rows, 1):
        path = config.LIBRARY_ROOT / row["library_path"]
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
