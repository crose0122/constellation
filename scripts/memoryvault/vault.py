"""LUKS vault helpers.

The passphrase is never stored, logged, read from config, or passed as an
argument — cryptsetup prompts for it interactively, and only the
household's owners know it. Nothing in this module (or anywhere else) writes any
reference to a vaulted item outside the container.
"""

import contextlib
import hashlib
import json
import os
import shutil
import stat as _stat
import subprocess
import time
from pathlib import Path

from . import config
from .db import bump_stat

MAPPER_NAME = "memoryvault"


class VaultUnavailable(Exception):
    pass


class VaultSourceMissing(Exception):
    """The library file for a flagged photo is gone. We refuse to scrub the
    DB in that case: scrubbing would erase the only record that the photo
    existed while no copy of it reached the vault."""


class VaultIntegrityError(Exception):
    """The vault copy does not hash to the photo's sha256. Nothing scrubbed."""


class VaultCleanupIncomplete(Exception):
    """The DB scrub committed but a library file could not be removed (or
    its removal could not be made durable). The move journal is kept, so
    recover_interrupted() finishes the job."""


class VaultBusy(VaultUnavailable):
    """Another process (or thread) holds the vault lock: a move or a
    recovery is in flight. Nothing was touched; try again."""


class DurabilityError(OSError):
    """A durability barrier (fsync of a file or directory) failed. Treated
    as fatal before any irreversible step: no scrub, no deletion."""


# Where is_mounted() looks for the mount's backing device. Module-level so
# tests can point them at a fake /proc + /sys tree.
MOUNTINFO = Path("/proc/self/mountinfo")
SYS_DEV_BLOCK = Path("/sys/dev/block")

# Inside the vault (so inside the encrypted container): the move journal and
# in-flight copies. Dot-names keep them out of folder listings.
PENDING_DIR = ".pending"
NOTES_DIR = ".notes"

# One lock serialises EVERY vault mover and recovery, across processes and
# threads (web worker, screen, rescreen, `vault open`, the CLI review verbs).
# Without it, recovery running in one process could read a live mover's
# journal as "died before commit", delete its verified vault copy, and the
# mover would then scrub the DB and delete the original: photo gone
# everywhere. Seconds to wait for the lock:
LOCK_TIMEOUT = 120.0            # movers: wait for the in-flight one to finish
RECOVERY_LOCK_TIMEOUT = 10.0    # recovery: a held lock means the journal is live


def vault_subdirs() -> tuple[str, ...]:
    return ("", "review", *config.VAULT_FOLDERS)


VAULT_SUBDIRS = vault_subdirs()   # import-time snapshot, kept for callers


def is_mounted() -> bool:
    """Is there somewhere safe for flagged content to go?

    'luks' (the default, and what we run at home): only a genuinely mounted
    LUKS volume counts. A bare directory must NOT pass — writing flagged
    photos to an unmounted mountpoint would leave them in plaintext.

    'dir': VAULT_MOUNT is a plain directory that is always available, for
    containers and Windows where the host disk is already encrypted.

    MEMORYVAULT_VAULT_MODE was documented in the README, shipped in
    .env.example, baked into the Dockerfile and written by the setup wizard —
    but nothing ever read it, so `os.path.ismount()` decided every case. A
    plain directory is never a mountpoint, so dir mode always reported "vault
    not mounted": `screen` aborted, no photo ever reached 'screened', and
    since `tag` only takes screened photos, nothing was ever tagged. Every
    Docker and native install produced a permanently empty star map.
    """
    if config.VAULT_MODE == "dir":
        try:
            for sub in vault_subdirs():
                (config.VAULT_MOUNT / sub).mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        return config.VAULT_MOUNT.is_dir()
    # luks: "something is mounted here" is not enough. A USB stick, a tmpfs
    # or a bind mount of a plain folder is also a mountpoint, and would get
    # flagged photos written to it in plaintext. The mount's backing block
    # device has to be dm-crypt (directly, or underneath LVM).
    if not os.path.ismount(config.VAULT_MOUNT):
        return False
    return backing_is_encrypted(config.VAULT_MOUNT)


def _unescape_mountinfo(field: str) -> str:
    # mountinfo octal-escapes space, tab, newline and backslash
    import re
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def mount_device(mount: Path) -> str | None:
    """'major:minor' of the device mounted at `mount`, or None. If several
    mounts stack on the same point, the last (topmost) one wins — that is the
    one a write actually lands on."""
    try:
        target = os.path.realpath(mount)
        lines = MOUNTINFO.read_text().splitlines()
    except OSError:
        return None
    dev = None
    for line in lines:
        parts = line.split()
        if len(parts) >= 5 and _unescape_mountinfo(parts[4]) == target:
            dev = parts[2]
    return dev


def _is_dm_crypt(majmin: str, depth: int = 0) -> bool:
    if depth > 8:
        return False
    node = SYS_DEV_BLOCK / majmin
    try:
        if (node / "dm" / "uuid").read_text().strip().startswith("CRYPT-"):
            return True
    except OSError:
        pass
    try:
        slaves = list((node / "slaves").iterdir())
    except OSError:
        return False
    for slave in slaves:
        try:
            child = (slave / "dev").read_text().strip()
        except OSError:
            continue
        if _is_dm_crypt(child, depth + 1):
            return True
    return False


def backing_is_encrypted(mount: Path) -> bool:
    """True only if the filesystem at `mount` sits on a dm-crypt device."""
    dev = mount_device(mount)
    return bool(dev) and _is_dm_crypt(dev)


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
    for sub in vault_subdirs():
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
        if not p.is_file() or PENDING_DIR in p.relative_to(
                config.VAULT_MOUNT).parts:
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
    with vault_lock():
        return _release_locked(conn, filename)


def _release_locked(conn, filename: str) -> dict:
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

    # Screening flags videos too, so the review queue holds them — and PIL
    # cannot open a .mov. Releasing one used to raise, and since a release is
    # usually issued as a batch, one video would abort the rest of the batch.
    if src.suffix.lower() in config.VIDEO_EXTENSIONS:
        return _release_video(conn, src, sha, filename)

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


def _release_video(conn, src, sha: str, filename: str) -> dict:
    """The video half of release_from_review: poster frame instead of a PIL
    decode, media_kind='video', and the duration recorded — mirroring what
    ingest does, so a released video behaves like any other in the library."""
    from datetime import datetime

    from PIL import Image

    from .ingest import library_dest, make_thumbnail
    from . import video as vid

    meta = vid.probe(str(src))
    dest = library_dest(sha, src, meta.get("taken_at"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))

    poster = vid.make_poster(str(dest), sha, meta.get("duration"))
    if poster is None:
        # leave it in the library but say so: re-running poster extraction is
        # cheap, whereas moving it back into the vault is not
        raise RuntimeError(f"poster extraction failed for {filename} (ffmpeg?)")
    with Image.open(poster) as pim:
        make_thumbnail(pim, sha)

    conn.execute(
        "INSERT INTO photos (sha256, phash, width, height, taken_at, camera, "
        "gps_lat, gps_lon, media_kind, status, library_path, duration, "
        "created_at) VALUES (?, NULL, ?, ?, ?, NULL, ?, ?, 'video', "
        "'screened', ?, ?, ?)",
        (sha, meta.get("width"), meta.get("height"), meta.get("taken_at"),
         meta.get("gps_lat"), meta.get("gps_lon"),
         str(dest.relative_to(config.LIBRARY_ROOT)), meta.get("duration"),
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return {"released": filename, "sha256": sha[:16], "kind": "video",
            "duration": meta.get("duration")}


def delete_from_review(filename: str) -> dict:
    """Human verdict: it's garbage. Shred it — per-photo human approval is
    the project's required bar for deletion, and this button press is it."""
    from .migrate import _shred

    with vault_lock():
        src = config.VAULT_MOUNT / "review" / filename
        if not src.is_file():
            raise FileNotFoundError(src)
        _shred(src)
    return {"deleted": filename}


def keep_in_vault(filename: str) -> dict:
    """Human verdict: it belongs in the vault. Move review/ -> vault root."""
    with vault_lock():
        src = config.VAULT_MOUNT / "review" / filename
        if not src.is_file():
            raise FileNotFoundError(src)
        dst = config.VAULT_MOUNT / filename
        if dst.exists():
            dst = config.VAULT_MOUNT / f"dup-{filename}"
        shutil.move(str(src), str(dst))
    return {"kept": filename}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_dir(d: Path):
    """Make a directory's entries (creates, renames, unlinks) durable.

    Fails CLOSED: any error raises DurabilityError, and callers must not go
    on to an irreversible step (scrub, deletion, journal retirement). The one
    exception is Windows, where a directory cannot be opened for fsync at all
    and NTFS journals its own metadata."""
    if os.name == "nt":
        return
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError as e:
        raise DurabilityError(e.errno, f"cannot open {d} for fsync: {e}") from e
    try:
        os.fsync(fd)
    except OSError as e:
        raise DurabilityError(e.errno, f"fsync({d}) failed: {e}") from e
    finally:
        os.close(fd)


def _mkdir_durable(d: Path):
    """mkdir that also makes the directory entry durable in its parent.

    The parent barrier runs EVERY time, even when `d` already exists: an
    earlier attempt may have created `d` and then failed its barrier, and
    existence alone does not prove the entry survives a power cut. Callers
    rely on `d` before destructive progress, so we pay one fsync to be sure."""
    d.mkdir(exist_ok=True)
    _fsync_dir(d.parent)


def _stat_strict(p: Path, follow: bool = True):
    """os.stat that only treats "not there" as "not there".

    Path.is_dir()/is_file()/exists() swallow EVERY OSError (EIO included on
    Python 3.14) and answer False. In this module that turns "the disk could
    not tell us" into "nothing to do" and lets a journal retire under metadata
    uncertainty. Here only ENOENT/ENOTDIR mean absent; anything else raises
    and the caller keeps its journal."""
    try:
        return os.stat(p, follow_symlinks=follow)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _is_dir_strict(p: Path) -> bool:
    st = _stat_strict(p)
    return st is not None and _stat.S_ISDIR(st.st_mode)


def _is_file_strict(p: Path) -> bool:
    st = _stat_strict(p)
    return st is not None and _stat.S_ISREG(st.st_mode)


def _lexists_strict(p: Path) -> bool:
    """exists() or is_symlink(), without hiding metadata errors."""
    return _stat_strict(p, follow=False) is not None


def _barrier(touched: set, d: Path):
    """Queue a directory barrier if the directory exists. Cleanup queues the
    parent of every path it is responsible for, whether or not THIS pass
    changed it: a retry after a failed barrier finds the file already gone,
    and only repeating the barrier proves the removal is durable. A stat
    error is NOT "missing": it raises, so the barrier is never forgotten."""
    if _is_dir_strict(d):
        touched.add(d)


def _write_durable(path: Path, data: bytes):
    """Publish `data` at `path` atomically and durably: temp file in the same
    directory, fsync it, rename over, fsync the directory. Either the old
    contents or the new ones survive a power cut, never a torn file."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _write_json_durable(path: Path, data: dict):
    _write_durable(path, json.dumps(data).encode())


def _lock_path() -> Path:
    # Next to the DB: every process that moves or recovers vault items
    # shares the DB, and the lock file holds no data (nothing leaks outside
    # the container), so the vault listing stays clean.
    db = Path(config.DB_PATH)
    return db.with_name(db.name + ".vault-lock")


def _try_lock(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock(fd: int):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def vault_lock(timeout: float | None = None):
    """Exclusive cross-process vault lock. NOT re-entrant: every acquisition
    opens its own descriptor, so a nested acquisition (even on the same
    thread) waits like any other process would. A crashed holder's lock is
    released by the kernel, which is exactly what lets recovery run later.
    Raises VaultBusy if it can't be had within `timeout` seconds."""
    timeout = LOCK_TIMEOUT if timeout is None else timeout
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise VaultBusy("another vault move or recovery is in "
                                "progress; nothing was touched")
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def _true_name(src: Path) -> str:
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
    return name


def _note_rel(conn, row) -> str | None:
    """Obsidian note path (relative to MEMORYVAULT_ROOT, no .md) that
    notes.generate() writes for this photo — same year rule as notes.py."""
    if not row["library_path"]:
        return None
    year = (row["taken_at"] or "")[:4]
    if not year:
        t = conn.execute("SELECT value FROM tags WHERE photo_id = ? AND "
                         "dimension = 'year' LIMIT 1", (row["id"],)).fetchone()
        year = t["value"] if t else "Unknown"
    return f"Photos/{year}/{Path(row['library_path']).stem}"


def _scrub_db(conn, photo_id: int, sha: str, review: bool):
    """Ledger + scrub. Every table that can carry a trace of the photo. Runs
    inside the caller's transaction; does not commit."""
    def table(name):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name=?", (name,)).fetchone() is not None

    # sha ledger BEFORE the scrub: ingest consults it so a source copy that
    # still exists somewhere (backup mirror, USB drive) can never re-ingest a
    # vaulted photo back into the open library
    conn.execute("CREATE TABLE IF NOT EXISTS vaulted ("
                 "sha256 TEXT PRIMARY KEY, vaulted_at TEXT)")
    conn.execute("INSERT OR IGNORE INTO vaulted VALUES (?, datetime('now'))",
                 (sha,))

    # scrub: no row, tag, face, edge, caption, OCR, placard, error or filename
    # reference survives. Children first (duplicate_members hangs off files
    # and duplicate_groups, and foreign_keys=ON would refuse the parent).
    if table("duplicate_members"):
        conn.execute("DELETE FROM duplicate_members WHERE file_id IN "
                     "(SELECT id FROM files WHERE photo_id = ?)", (photo_id,))
        conn.execute("DELETE FROM duplicate_members WHERE group_id IN "
                     "(SELECT id FROM duplicate_groups WHERE keeper_photo_id = ?)",
                     (photo_id,))
    if table("face_bans") and table("faces"):
        conn.execute("DELETE FROM face_bans WHERE face_id IN "
                     "(SELECT id FROM faces WHERE photo_id = ?)", (photo_id,))
    for tbl, col in (("duplicate_groups", "keeper_photo_id"),
                     ("faces", "photo_id"), ("tags", "photo_id"),
                     ("embeddings", "photo_id"), ("descriptions", "photo_id"),
                     ("placards", "photo_id"), ("errors", "photo_id"),
                     ("files", "photo_id")):
        if table(tbl):
            conn.execute(f"DELETE FROM {tbl} WHERE {col} = ?", (photo_id,))
    if table("photo_edges"):
        conn.execute(
            "DELETE FROM photo_edges WHERE photo_id_a = ? OR photo_id_b = ?",
            (photo_id, photo_id))
    conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    bump_stat(conn, "review_total" if review else "vaulted_total")


def _cleanup_library(journal: dict) -> list[str]:
    """After the scrub committed: remove the plaintext original and every
    derivative. Returns the paths that could NOT be removed.

    Every removal is made durable (directory fsync) before this returns
    success, so the caller only retires the journal once a power cut can no
    longer bring a plaintext directory entry back."""
    sha = journal["sha256"]
    failed = []
    touched: set[Path] = set()      # directories whose entries we changed
    src = config.LIBRARY_ROOT / journal["source_path"]
    try:
        # only ever delete OUR bytes: a different file now at that path stays
        if _is_file_strict(src) and _sha256(src) == sha:
            src.unlink()
        _barrier(touched, src.parent)
    except OSError:
        failed.append(str(src))
    targets = [config.LIBRARY_ROOT / rel
               for rel in journal.get("derivative_paths", [])]
    targets += [config.LIBRARY_ROOT / "thumbnails" / f"{sha[:16]}.jpg",
                config.LIBRARY_ROOT / "display" / f"{sha[:16]}.jpg"]
    targets += [config.LIBRARY_ROOT / "face-crops" / f"{i}.jpg"
                for i in journal.get("face_ids", [])]
    for f in targets:
        try:
            if _lexists_strict(f):
                f.unlink()
            _barrier(touched, f.parent)
        except OSError:
            failed.append(str(f))
    # video screening/caption frame caches
    for d in (f"screenframes-{sha[:16]}", f"capframes-{sha[:16]}"):
        cache = config.LIBRARY_ROOT / "staging" / d
        try:
            if _lexists_strict(cache):
                shutil.rmtree(cache, ignore_errors=True)
                if _lexists_strict(cache):
                    failed.append(str(cache))
            _barrier(touched, cache.parent)
        except OSError:
            failed.append(str(cache))
    # the Obsidian note: may hold a human-written manual block, so it moves
    # INTO the vault rather than being destroyed; index notes lose the link.
    # The vault copy is durable BEFORE the original note is unlinked.
    rel = journal.get("note_rel")
    if rel:
        note = config.MEMORYVAULT_ROOT / f"{rel}.md"
        try:
            if _is_file_strict(note):
                data = note.read_bytes()
                if f"photo: {journal.get('note_marker')}".encode() in data:
                    nd = config.VAULT_MOUNT / NOTES_DIR
                    _mkdir_durable(nd)
                    _write_durable(nd / f"{sha[:16]}.md", data)
                    note.unlink()
            _barrier(touched, note.parent)
        except OSError:
            failed.append(str(note))
        link = f"[[{rel}]]"
        try:
            index_files = list(config.MEMORYVAULT_ROOT.glob("*/*.md"))
        except OSError:
            index_files = []
        for idx in index_files:
            try:
                # queued even when the link is already gone: a prior pass
                # may have replaced the file and then failed its barrier
                _barrier(touched, idx.parent)
                text = idx.read_text()
                if link not in text or "type: index" not in text:
                    continue
                _write_durable(idx, "".join(
                    ln for ln in text.splitlines(keepends=True)
                    if link not in ln).encode())
            except OSError:
                failed.append(str(idx))
    for d in sorted(touched):
        try:
            _fsync_dir(d)
        except OSError:
            failed.append(f"{d} (fsync)")
    return failed


def route_to_vault(conn, photo_id: int, review: bool, dest: str | None = None):
    """Move a flagged photo into the mounted vault and scrub every trace of it
    from the database (the vault-wall invariant). Only aggregate counters remain.
    dest picks a named vault folder (config.VAULT_FOLDERS) for user moves.

    The whole move runs under vault_lock(), shared with recover_interrupted()
    and every other mover, so recovery can never mistake a LIVE move for a
    dead one. Crash-safe ordering, journalled inside the vault:
      1. journal written durably    (nothing else touched yet)
      2. COPY original -> vault, fsync, verify sha256 against the DB row,
         publish without overwriting, fsync the directories
      3. ledger + scrub, COMMIT     (this function is a commit boundary)
      4. delete the plaintext original + every derivative, fsync dirs
      5. journal removed, fsync
    A failure before 3 rolls back: vault copy removed, library untouched. A
    failed durability barrier before 3 is a failure. A crash anywhere is
    finished or undone by recover_interrupted(). The source is never deleted
    before a verified, durable vault copy exists and the scrub committed, and
    the DB is never scrubbed without one."""
    if not is_mounted():
        raise VaultUnavailable(f"vault not mounted at {config.VAULT_MOUNT}")
    if conn.in_transaction:
        conn.commit()       # the caller's earlier writes are not ours to undo
    with vault_lock():
        _route_locked(conn, photo_id, review, dest)


def _route_locked(conn, photo_id: int, review: bool, dest: str | None):
    row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if row is None:
        return
    sha = row["sha256"]
    pending = config.VAULT_MOUNT / PENDING_DIR
    if (pending / f"{sha}.json").exists():
        # a dead mover left a journal for these same bytes: settle it first
        # (we hold the lock, so it really is dead), then look again
        _recover_locked(conn, {"completed": 0, "rolled_back": 0,
                               "needs_attention": []})
        if (pending / f"{sha}.json").exists():
            raise VaultCleanupIncomplete(
                f"photo {photo_id}: an earlier interrupted move of these "
                "bytes needs a human (see <vault>/.pending/)")
        row = conn.execute("SELECT * FROM photos WHERE id = ?",
                           (photo_id,)).fetchone()
        if row is None:
            return
        sha = row["sha256"]
    keys = row.keys()
    # The file whose bytes ARE the photo's sha256. For a HEIC that is the
    # untouched original (original_path); library_path is only its JPEG
    # working rendition, which goes as a derivative. Vaulting just the
    # rendition used to leave the original HEIC behind in plaintext.
    orig = row["original_path"] if "original_path" in keys else None
    source_rel = orig or row["library_path"]
    derivatives = [row["library_path"]] if (
        orig and row["library_path"] and row["library_path"] != orig) else []
    src = config.LIBRARY_ROOT / (source_rel or "")
    if not source_rel or not src.is_file():
        raise VaultSourceMissing(
            f"photo {photo_id}: library file missing; DB left intact")

    sub = "review" if review else (dest if dest in config.VAULT_FOLDERS else "")
    dest_dir = config.VAULT_MOUNT / sub
    if sub:                 # the mount root itself exists and is not ours
        _mkdir_durable(dest_dir)
    _mkdir_durable(pending)

    name = _true_name(src)
    dst = dest_dir / name
    if dst.exists():
        dst = dest_dir / f"{sha[:8]}-{name}"
    n = 1
    while dst.exists():
        dst = dest_dir / f"{sha[:8]}-{n}-{name}"
        n += 1

    face_ids = []
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='faces'").fetchone():
        face_ids = [r[0] for r in conn.execute(
            "SELECT id FROM faces WHERE photo_id = ?", (photo_id,))]
    journal = {"sha256": sha, "photo_id": photo_id, "review": bool(review),
               "source_path": source_rel, "derivative_paths": derivatives,
               "note_marker": row["library_path"],
               "vault_path": str(dst.relative_to(config.VAULT_MOUNT)),
               "face_ids": face_ids, "note_rel": _note_rel(conn, row)}
    jpath = pending / f"{sha}.json"
    part = pending / f"{sha}.part"
    published = False       # only ever unlink a dst WE created
    try:
        _write_json_durable(jpath, journal)
        shutil.copyfile(src, part)
        with open(part, "rb") as f:
            os.fsync(f.fileno())
        if _sha256(part) != sha:
            raise VaultIntegrityError(
                f"photo {photo_id}: vault copy does not match its sha256")
        _publish_no_overwrite(part, dst)
        published = True
        _fsync_dir(dest_dir)
        _fsync_dir(pending)

        conn.execute("PRAGMA secure_delete = ON")   # zero freed pages
        _scrub_db(conn, photo_id, sha, review)
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        part.unlink(missing_ok=True)
        try:
            intact = conn.execute("SELECT 1 FROM photos WHERE id = ?",
                                  (photo_id,)).fetchone() is not None
        except Exception:
            intact = None   # can't tell: leave copy + journal for recovery
        if intact:
            try:
                if published:
                    dst.unlink(missing_ok=True)     # DB intact -> undo the copy
                    _fsync_dir(dest_dir)
                jpath.unlink(missing_ok=True)
                _drop_if_empty(pending)
            except OSError:
                pass        # journal left behind: recovery settles it
        raise

    failed = _cleanup_library(journal)
    if failed:
        raise VaultCleanupIncomplete(
            f"photo {photo_id}: {len(failed)} library file(s) not removed "
            "durably; journal kept for recover_interrupted()")
    jpath.unlink()
    try:
        _fsync_dir(pending)
    except OSError as e:
        # everything is done; at worst the journal reappears after a power
        # cut and recovery re-runs the (idempotent) cleanup
        raise VaultCleanupIncomplete(
            f"photo {photo_id}: journal retirement not durable ({e})") from e
    _drop_if_empty(pending)


def _publish_no_overwrite(part: Path, dst: Path):
    """Give the verified copy its final name WITHOUT ever replacing an
    existing file (a same-name file that appeared after we picked `dst`
    stays untouched; we fail and roll back instead). Hard link + unlink is
    atomic no-clobber; filesystems without hard links fall back to an
    existence check + rename, which the vault lock makes safe against every
    locked writer."""
    try:
        os.link(part, dst)
    except FileExistsError:
        raise
    except OSError:
        if dst.exists() or dst.is_symlink():
            raise FileExistsError(f"{dst} appeared during the move")
        os.replace(part, dst)
        return
    part.unlink()


def _drop_if_empty(d: Path):
    try:
        d.rmdir()       # only succeeds when empty — keeps the vault tidy
    except OSError:
        pass


def recover_interrupted(conn, timeout: float | None = None) -> dict:
    """Finish or undo any route_to_vault() that died part-way (power cut,
    kill -9, disk error). Safe to run any time the vault is open; screen and
    rescreen run it first. Never deletes a library file unless a vault copy
    with the right sha256 exists and the DB scrub is committed.

    Runs under vault_lock(). A journal is only ever acted on while we hold
    the lock, and a live mover holds the lock from before its journal exists
    until after it is retired, so every journal we see is genuinely dead. If
    the lock is busy (a move is in flight right now) we touch nothing and
    return with "busy": True; the next screen / vault-open picks it up."""
    out = {"completed": 0, "rolled_back": 0, "needs_attention": []}
    if not is_mounted():
        return out
    if not (config.VAULT_MOUNT / PENDING_DIR).is_dir():
        return out
    acquired = False
    try:
        with vault_lock(RECOVERY_LOCK_TIMEOUT if timeout is None else timeout):
            acquired = True
            return _recover_locked(conn, out)
    except VaultBusy:
        if acquired:
            raise
        out["busy"] = True
        return out


def _recover_locked(conn, out: dict) -> dict:
    """recover_interrupted's body. Caller MUST hold vault_lock()."""
    pending = config.VAULT_MOUNT / PENDING_DIR
    if not pending.is_dir():
        return out
    if conn.in_transaction:
        conn.commit()
    for jpath in sorted(pending.glob("*.json")):
        try:
            j = json.loads(jpath.read_text())
            sha = j["sha256"]
        except (OSError, ValueError, KeyError):
            out["needs_attention"].append(jpath.name)
            continue
        try:
            (pending / f"{sha}.part").unlink(missing_ok=True)
            vcopy = config.VAULT_MOUNT / j["vault_path"]
            vault_ok = _is_file_strict(vcopy) and _sha256(vcopy) == sha
            row = conn.execute("SELECT id FROM photos WHERE sha256 = ?",
                               (sha,)).fetchone()
            src = config.LIBRARY_ROOT / (j.get("source_path") or "")
            src_ok = bool(j.get("source_path")) and _is_file_strict(src) \
                and _sha256(src) == sha
            if row is not None and src_ok:
                # scrub never committed and the original is intact: undo.
                # The removal is durable before the journal goes.
                # The barrier repeats even when the copy is already gone: an
                # earlier rollback may have unlinked it and failed to fsync.
                if vault_ok:
                    vcopy.unlink()
                if _is_dir_strict(vcopy.parent):
                    _fsync_dir(vcopy.parent)
                jpath.unlink()
                _fsync_dir(pending)
                out["rolled_back"] += 1
                continue
        except OSError:
            out["needs_attention"].append(sha[:16])
            continue
        if not vault_ok:
            # no trustworthy vault copy: touch nothing, a human decides
            out["needs_attention"].append(sha[:16])
            continue
        try:
            _fsync_dir(vcopy.parent)    # the copy must be durable before any
        except OSError:                 # scrub or library deletion, every pass
            out["needs_attention"].append(sha[:16])
            continue
        if row is not None:
            # vault copy verified but the original is gone: roll forward
            try:
                conn.execute("PRAGMA secure_delete = ON")
                _scrub_db(conn, row["id"], sha, j.get("review", False))
                conn.commit()
            except Exception:
                conn.rollback()
                out["needs_attention"].append(sha[:16])
                continue
        if _cleanup_library(j):
            out["needs_attention"].append(sha[:16])
            continue
        try:
            jpath.unlink()
            _fsync_dir(pending)
        except OSError:
            out["needs_attention"].append(sha[:16])
            continue
        out["completed"] += 1
    _drop_if_empty(pending)
    return out
