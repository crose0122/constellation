"""Stage 1 — Discover (SPEC.md §5.1). Read-only walk of a source root;
rows land in `files` with disposition='discovered'. Never touches sources."""

from pathlib import Path

from . import config
from .db import now, record_error, start_run, finish_run


def register_source(conn, root: Path, kind: str = "local", description: str = "") -> int:
    row = conn.execute("SELECT id FROM sources WHERE root = ?", (str(root),)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO sources(kind, root, description) VALUES (?,?,?)",
        (kind, str(root), description),
    )
    conn.commit()
    return cur.lastrowid


def media_kind(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in config.IMAGE_EXTENSIONS:
        return "photo"
    if ext in config.VIDEO_EXTENSIONS:
        return "video"
    return None


def discover(conn, root: Path, kind: str = "local", description: str = "") -> dict:
    source_id = register_source(conn, root, kind, description)
    run = start_run(conn, "discover")
    stats = {"seen": 0, "new": 0, "errors": 0}

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        mk = media_kind(path)
        if mk is None:
            continue
        stats["seen"] += 1
        try:
            st = path.stat()
            cur = conn.execute(
                "INSERT OR IGNORE INTO files"
                "(source_id, source_path, size, mtime, media_kind, disposition, discovered_at) "
                "VALUES (?,?,?,?,?,'discovered',?)",
                (source_id, str(path), st.st_size, str(st.st_mtime), mk, now()),
            )
            stats["new"] += cur.rowcount
        except OSError as e:
            stats["errors"] += 1
            record_error(conn, "discover", str(e), source_path=path)

    conn.execute("UPDATE sources SET last_scan_at = ? WHERE id = ?", (now(), source_id))
    conn.commit()
    finish_run(conn, run, stats)
    return stats
