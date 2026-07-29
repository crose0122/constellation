"""LUKS vault helpers (SPEC.md §6).

The passphrase is never stored, logged, read from config, or passed as an
argument — cryptsetup prompts for it interactively, and only the
household's owners know it. Nothing in this module (or anywhere else) writes any
reference to a vaulted item outside the container.
"""

import os
import shutil
import subprocess
from pathlib import Path

from . import config
from .db import bump_stat

MAPPER_NAME = "memoryvault"


class VaultUnavailable(Exception):
    pass


def is_mounted() -> bool:
    return os.path.ismount(config.VAULT_MOUNT)


def open_vault():
    """Interactive: cryptsetup prompts for the passphrase on the terminal."""
    config.VAULT_MOUNT.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["sudo", "cryptsetup", "open", str(config.VAULT_IMG), MAPPER_NAME],
        check=True,
    )
    subprocess.run(
        ["sudo", "mount", f"/dev/mapper/{MAPPER_NAME}", str(config.VAULT_MOUNT)],
        check=True,
    )
    # a fresh ext4 root is owned by root (mkfs ran under sudo) — hand it to
    # the pipeline user or route_to_vault dies on its first flagged photo
    subprocess.run(
        ["sudo", "chown", f"{os.getenv('USER', 'root')}:", str(config.VAULT_MOUNT)],
        check=True,
    )
    for sub in ("", "review", "casey", "other"):
        (config.VAULT_MOUNT / sub).mkdir(exist_ok=True)


def backfill_ledger(conn) -> int:
    """Hash everything in the mounted vault into the `vaulted` sha ledger.
    Covers photos vaulted before the ledger existed; runs at vault-open."""
    import hashlib

    if not is_mounted():
        return 0
    conn.execute("CREATE TABLE IF NOT EXISTS vaulted ("
                 "sha256 TEXT PRIMARY KEY, vaulted_at TEXT)")
    added = 0
    for p in config.VAULT_MOUNT.rglob("*"):
        if not p.is_file():
            continue
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        cur = conn.execute(
            "INSERT OR IGNORE INTO vaulted VALUES (?, datetime('now'))",
            (h.hexdigest(),))
        added += cur.rowcount
    conn.commit()
    return added


def close_vault():
    subprocess.run(["sudo", "umount", str(config.VAULT_MOUNT)], check=True)
    subprocess.run(["sudo", "cryptsetup", "close", MAPPER_NAME], check=True)


def create_vault(size_gb: int = 50):
    """One-time interactive creation. luksFormat prompts for the passphrase."""
    img = config.VAULT_IMG
    if img.exists():
        raise VaultUnavailable(f"{img} already exists")
    img.parent.mkdir(parents=True, exist_ok=True)
    with open(img, "wb") as f:
        f.truncate(size_gb * 1024**3)
    subprocess.run(
        ["sudo", "cryptsetup", "luksFormat", "--type", "luks2", str(img)], check=True
    )
    subprocess.run(
        ["sudo", "cryptsetup", "open", str(img), MAPPER_NAME], check=True
    )
    subprocess.run(["sudo", "mkfs.ext4", f"/dev/mapper/{MAPPER_NAME}"], check=True)
    subprocess.run(["sudo", "cryptsetup", "close", MAPPER_NAME], check=True)


def release_from_review(conn, filename: str) -> dict:
    """Human verdict: a review-queue photo is fine. Move it out of the vault
    and into originals/ with status='screened' (bypassing re-screening —
    the human IS the second opinion), so tagging picks it up naturally."""
    from datetime import datetime

    from PIL import Image, ImageOps

    from .ingest import extract_exif, library_dest, make_thumbnail, sha256_file

    src = config.VAULT_MOUNT / "review" / filename
    if not src.is_file():
        raise FileNotFoundError(src)
    sha = sha256_file(src)
    existing = conn.execute(
        "SELECT id FROM photos WHERE sha256 = ?", (sha,)).fetchone()
    if existing:
        src.unlink()
        return {"released": filename, "note": "already in library"}
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        exif = extract_exif(img)
        make_thumbnail(img, sha)
        width, height = img.size
    dest = library_dest(sha, src, exif.get("taken_at"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    conn.execute(
        "INSERT INTO photos (sha256, phash, width, height, taken_at, camera, "
        "gps_lat, gps_lon, media_kind, status, library_path, created_at) "
        "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 'photo', 'screened', ?, ?)",
        (sha, width, height, exif.get("taken_at"), exif.get("camera"),
         exif.get("gps_lat"), exif.get("gps_lon"),
         str(dest.relative_to(config.LIBRARY_ROOT)),
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return {"released": filename, "sha256": sha[:16]}


def delete_from_review(filename: str) -> dict:
    """Human verdict: it's garbage. Shred it — per-photo human approval is
    the project's required bar for deletion, and this button press is it."""
    from .migrate import _shred

    src = config.VAULT_MOUNT / "review" / filename
    if not src.is_file():
        raise FileNotFoundError(src)
    _shred(src)
    return {"deleted": filename}


def keep_in_vault(filename: str) -> dict:
    """Human verdict: it belongs in the vault. Move review/ -> vault root."""
    src = config.VAULT_MOUNT / "review" / filename
    if not src.is_file():
        raise FileNotFoundError(src)
    dst = config.VAULT_MOUNT / filename
    if dst.exists():
        dst = config.VAULT_MOUNT / f"dup-{filename}"
    shutil.move(str(src), str(dst))
    return {"kept": filename}


def route_to_vault(conn, photo_id: int, review: bool, dest: str | None = None):
    """Move a flagged photo into the mounted vault and scrub every trace of it
    from the database (SPEC.md invariant #3). Only aggregate counters remain.
    dest picks a vault subfolder ('casey'/'other') for user-initiated moves."""
    if not is_mounted():
        raise VaultUnavailable(f"vault not mounted at {config.VAULT_MOUNT}")

    row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if row is None:
        return
    sub = "review" if review else (dest if dest in ("casey", "other") else "")
    dest_dir = config.VAULT_MOUNT / sub
    dest_dir.mkdir(exist_ok=True)

    src = config.LIBRARY_ROOT / row["library_path"]
    if src.exists():
        # source names routinely lie about the format (JPEG bytes named .png
        # from app exports/web caches) — vault files are viewed raw, so give
        # the file its true extension on the way in
        name = src.name
        try:
            from PIL import Image

            with Image.open(src) as im:
                real = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif",
                        "WEBP": ".webp", "HEIF": ".heic", "BMP": ".bmp",
                        "TIFF": ".tiff"}.get(im.format)
            if real and src.suffix.lower() not in (real, ".jpeg" if real == ".jpg" else real):
                name = src.stem + real
        except Exception:
            pass
        dst = dest_dir / name
        if dst.exists():
            dst = dest_dir / f"{row['sha256'][:8]}-{name}"
        shutil.move(str(src), str(dst))

    thumb = config.LIBRARY_ROOT / "thumbnails" / f"{row['sha256'][:16]}.jpg"
    thumb.unlink(missing_ok=True)
    display = config.LIBRARY_ROOT / "display" / f"{row['sha256'][:16]}.jpg"
    display.unlink(missing_ok=True)
    has_faces = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='faces'"
    ).fetchone()
    if has_faces:
        for fc in conn.execute(
                "SELECT id FROM faces WHERE photo_id = ?",
                (photo_id,)).fetchall():
            (config.LIBRARY_ROOT / "face-crops" / f"{fc['id']}.jpg").unlink(
                missing_ok=True)

    # sha ledger BEFORE the scrub: ingest consults it so a source copy that
    # still exists somewhere (backup mirror, USB drive) can never re-ingest a
    # vaulted photo back into the open library
    conn.execute("CREATE TABLE IF NOT EXISTS vaulted ("
                 "sha256 TEXT PRIMARY KEY, vaulted_at TEXT)")
    conn.execute("INSERT OR IGNORE INTO vaulted VALUES (?, datetime('now'))",
                 (row["sha256"],))

    # scrub: no row, tag, face, edge, or filename reference survives
    if has_faces:
        conn.execute("DELETE FROM faces WHERE photo_id = ?", (photo_id,))
    conn.execute("DELETE FROM tags WHERE photo_id = ?", (photo_id,))
    conn.execute(
        "DELETE FROM photo_edges WHERE photo_id_a = ? OR photo_id_b = ?",
        (photo_id, photo_id),
    )
    conn.execute("DELETE FROM embeddings WHERE photo_id = ?", (photo_id,))
    conn.execute("DELETE FROM files WHERE photo_id = ?", (photo_id,))
    conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    bump_stat(conn, "review_total" if review else "vaulted_total")
