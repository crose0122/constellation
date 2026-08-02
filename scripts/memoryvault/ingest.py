"""Stage 2 — Ingest (SPEC.md §5.2). Copy to staging, hash, EXIF, then move
into originals/. Source files are never modified. Identity is sha256."""

import hashlib
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps
import imagehash

from . import config
from .db import now, record_error, start_run, finish_run

# attempts before a file is dead-lettered (disposition='failed') instead of
# being re-read by every nightly run. Some sources hold bytes that will never
# be an image — zero-length copies, truncated JPEGs, thumbnail caches — and
# retrying them forever buries real errors. `mvault retry` releases them.
MAX_ATTEMPTS = 3

EXIF_IFD = 0x8769
GPS_IFD = 0x8825
TAG_DATETIME_ORIGINAL = 36867
TAG_DATETIME = 306
TAG_MODEL = 272


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _gps_to_deg(values, ref) -> float | None:
    try:
        d, m, s = (float(v) for v in values)
        deg = d + m / 60 + s / 3600
        return -deg if ref in ("S", "W") else deg
    except Exception:
        return None


def extract_exif(img: Image.Image) -> dict:
    out = {"taken_at": None, "camera": None, "gps_lat": None, "gps_lon": None}
    try:
        exif = img.getexif()
        raw_dt = None
        try:
            raw_dt = exif.get_ifd(EXIF_IFD).get(TAG_DATETIME_ORIGINAL)
        except Exception:
            pass
        raw_dt = raw_dt or exif.get(TAG_DATETIME)
        if raw_dt:
            try:
                out["taken_at"] = datetime.strptime(
                    str(raw_dt), "%Y:%m:%d %H:%M:%S"
                ).isoformat()
            except ValueError:
                pass
        model = exif.get(TAG_MODEL)
        if model:
            out["camera"] = str(model).strip()
        try:
            gps = exif.get_ifd(GPS_IFD)
            if gps:
                lat = _gps_to_deg(gps.get(2, ()), gps.get(1))
                lon = _gps_to_deg(gps.get(4, ()), gps.get(3))
                out["gps_lat"], out["gps_lon"] = lat, lon
        except Exception:
            pass
    except Exception:
        pass
    return out


def library_dest(sha256: str, source_path: Path, taken_at: str | None) -> Path:
    if taken_at:
        year, month = taken_at[0:4], taken_at[5:7]
    else:
        year, month = "unknown", "00"
    name = f"{sha256[:8]}-{source_path.name}"
    return config.LIBRARY_ROOT / "originals" / year / month / name


def make_thumbnail(img: Image.Image, sha256: str) -> None:
    thumb_dir = config.LIBRARY_ROOT / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    # bake the EXIF orientation into pixels — the tag is stripped on save,
    # so without this every portrait phone shot renders sideways
    t = ImageOps.exif_transpose(img)
    t.thumbnail((512, 512))
    t.convert("RGB").save(thumb_dir / f"{sha256[:16]}.jpg", "JPEG", quality=85)


def ingest_one(conn, file_row) -> str:
    """Process one discovered file row. Returns disposition set."""
    src = Path(file_row["source_path"])
    sha = sha256_file(src)

    # never resurrect a photo the family removed: vaulted shas live only in
    # the encrypted vault, purged shas are gone forever — a source copy that
    # survives on a backup mirror or USB drive must not re-enter the library
    for ledger in ("vaulted", "purged"):
        try:
            gone = conn.execute(
                f"SELECT 1 FROM {ledger} WHERE sha256 = ?", (sha,)).fetchone()
        except sqlite3.OperationalError:
            gone = None                      # ledger table not created yet
        if gone:
            conn.execute(
                "UPDATE files SET disposition = ? WHERE id = ?",
                (ledger, file_row["id"]),
            )
            return ledger

    existing = conn.execute(
        "SELECT id FROM photos WHERE sha256 = ?", (sha,)
    ).fetchone()
    if existing:
        # exact duplicate seen elsewhere: the files row IS the record (SPEC §5.3)
        conn.execute(
            "UPDATE files SET photo_id = ?, disposition = 'duplicate' WHERE id = ?",
            (existing["id"], file_row["id"]),
        )
        return "duplicate"

    if file_row["media_kind"] == "video":
        return _ingest_video(conn, file_row, src, sha)

    staging = config.LIBRARY_ROOT / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / f"{sha[:16]}-{src.name}"
    shutil.copy2(src, staged)

    try:
        with Image.open(staged) as img:
            width, height = img.size
            phash = str(imagehash.phash(img))
            exif = extract_exif(img)
            make_thumbnail(img, sha)
    except Exception:
        staged.unlink(missing_ok=True)
        raise

    dest = library_dest(sha, src, exif["taken_at"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staged), str(dest))

    cur = conn.execute(
        "INSERT INTO photos(sha256, phash, width, height, taken_at, camera, "
        "gps_lat, gps_lon, media_kind, status, library_path, created_at) "
        "VALUES (?,?,?,?,?,?,?,?, 'photo', 'staged', ?, ?)",
        (
            sha, phash, width, height, exif["taken_at"], exif["camera"],
            exif["gps_lat"], exif["gps_lon"],
            str(dest.relative_to(config.LIBRARY_ROOT)), now(),
        ),
    )
    conn.execute(
        "UPDATE files SET photo_id = ?, disposition = 'canonical' WHERE id = ?",
        (cur.lastrowid, file_row["id"]),
    )
    return "canonical"


def _ingest_video(conn, file_row, src: Path, sha: str) -> str:
    """A video becomes a photos row (media_kind='video') with a poster frame
    as its display rendition + thumbnail. The original is copied into the
    library like a photo; downstream stages read the poster via
    video.representative_image()."""
    from . import video as vid

    dest = library_dest(sha, src, None)  # refined below once we know taken_at
    meta = vid.probe(str(src))
    if meta.get("live_photo"):
        # the motion half of a Live Photo — the still is ingested on its own,
        # so don't clutter the library with a ~3s "video"
        conn.execute("UPDATE files SET disposition = 'live-photo' WHERE id = ?",
                     (file_row["id"],))
        return "live_photo"
    if meta["taken_at"]:
        dest = library_dest(sha, src, meta["taken_at"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)

    poster = vid.make_poster(str(dest), sha, meta["duration"])
    if poster is None:
        dest.unlink(missing_ok=True)
        raise RuntimeError("poster-frame extraction failed (ffmpeg?)")
    # thumbnail from the poster (same 512px treatment photos get)
    with Image.open(poster) as pim:
        make_thumbnail(pim, sha)

    cur = conn.execute(
        "INSERT INTO photos(sha256, phash, width, height, taken_at, camera, "
        "gps_lat, gps_lon, media_kind, status, library_path, duration, created_at) "
        "VALUES (?,?,?,?,?,?,?,?, 'video', 'staged', ?, ?, ?)",
        (
            sha, None, meta["width"], meta["height"], meta["taken_at"], None,
            meta["gps_lat"], meta["gps_lon"],
            str(dest.relative_to(config.LIBRARY_ROOT)), meta["duration"], now(),
        ),
    )
    conn.execute(
        "UPDATE files SET photo_id = ?, disposition = 'canonical' WHERE id = ?",
        (cur.lastrowid, file_row["id"]),
    )
    return "canonical"


def _dead_letter(conn, file_id: int, source_path: str) -> None:
    conn.execute(
        "UPDATE files SET disposition = 'failed' WHERE id = ?", (file_id,)
    )
    print(f"  gave up after {MAX_ATTEMPTS} attempts: {source_path}")


def release_dead_letters(conn) -> int:
    """Put every dead-lettered file back in the ingest queue (see cmd_retry)."""
    return conn.execute(
        "UPDATE files SET disposition = 'discovered' WHERE disposition = 'failed'"
    ).rowcount


def _dead_letter_exhausted(conn) -> int:
    """Retire files that already burned through their attempts in earlier runs
    (their history predates the retry_count, or they failed before this run)."""
    cur = conn.execute(
        "UPDATE files SET disposition = 'failed' WHERE disposition = 'discovered' "
        "AND source_path IN (SELECT source_path FROM errors WHERE stage = 'ingest' "
        "AND resolved = 0 AND retry_count >= ?)",
        (MAX_ATTEMPTS,),
    )
    return cur.rowcount


def ingest(conn, limit: int | None = None, sample: bool = False) -> dict:
    """Ingest all (or a random sample of) discovered photo AND video files."""
    run = start_run(conn, "ingest")
    retired = _dead_letter_exhausted(conn)
    if retired:
        print(f"  {retired} unreadable file(s) retired — `mvault retry` to release")
    order = "ORDER BY RANDOM()" if sample else "ORDER BY id"
    sql = (
        "SELECT * FROM files WHERE disposition='discovered' "
        "AND media_kind IN ('photo','video') "
        + order
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()

    stats = {"canonical": 0, "duplicate": 0, "live_photo": 0, "vaulted": 0, "purged": 0,
             "errors": 0, "failed": retired}
    for i, row in enumerate(rows, 1):
        try:
            stats[ingest_one(conn, row)] += 1
        except Exception as e:
            stats["errors"] += 1
            attempts = record_error(
                conn, "ingest", repr(e), source_path=row["source_path"])
            if attempts >= MAX_ATTEMPTS:
                _dead_letter(conn, row["id"], row["source_path"])
                stats["failed"] += 1
        if i % 50 == 0:
            conn.commit()
            print(f"  ingested {i}/{len(rows)}")
    conn.commit()
    finish_run(conn, run, stats)
    return stats
