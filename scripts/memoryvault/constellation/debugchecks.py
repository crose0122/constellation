"""Debug Console status checks (fleet debug-console contract v1).

The handful of things that most often break a Constellation box, each probed
in its own thread and the whole set bounded by a deadline, so
/api/debug/state answers in well under 3 s even when Ollama or the disk hangs.

Privacy: checks report STATUS ONLY — counts, ages, percentages, up/down. No
photo content, no captions, no face names, no paths (the vault mount path is
never echoed), nothing about what screening flagged.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .. import config

DEADLINE_S = 2.5
OLLAMA_TIMEOUT_S = 1.5
DISK_WARN_PCT = 90


def _age(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        # db.now() writes naive LOCAL time; compare like with like
        ref = datetime.now(timezone.utc) if t.tzinfo else datetime.now()
        s = max(0, int((ref - t).total_seconds()))
    except ValueError:
        return "?"
    if s < 90:
        return f"{s}s ago"
    if s < 5400:
        return f"{s // 60}m ago"
    if s < 172800:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _ro(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def check_db(db_path) -> dict:
    conn = _ro(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    finally:
        conn.close()
    return {"label": "Library DB", "ok": True, "detail": f"readable · {n} items"}


def check_pipeline(db_path) -> dict:
    conn = _ro(db_path)
    try:
        active = conn.execute(
            "SELECT stage, started_at FROM runs WHERE finished_at IS NULL "
            "ORDER BY id DESC LIMIT 1").fetchone()
        last = conn.execute(
            "SELECT stage, finished_at FROM runs WHERE finished_at IS NOT NULL "
            "ORDER BY id DESC LIMIT 1").fetchone()
        errors = conn.execute(
            "SELECT COUNT(*) FROM errors WHERE resolved = 0").fetchone()[0]
    finally:
        conn.close()
    bits = []
    if active:
        bits.append(f"running {active['stage']} (started {_age(active['started_at'])})")
    if last:
        bits.append(f"last {last['stage']} {_age(last['finished_at'])}")
    if not bits:
        bits.append("no runs yet")
    bits.append(f"{errors} open error{'s' if errors != 1 else ''}")
    return {"label": "Pipeline", "ok": None if errors else True,
            "detail": " · ".join(bits)}


def ollama_base() -> str:
    u = urlsplit(config.OLLAMA_URL)
    return f"{u.scheme or 'http'}://{u.netloc}"


def check_vision() -> dict:
    base = ollama_base()
    try:
        with urllib.request.urlopen(f"{base}/api/version",
                                    timeout=OLLAMA_TIMEOUT_S) as r:
            ver = json.loads(r.read(4096) or b"{}").get("version", "")
        return {"label": "Vision model", "ok": True,
                "detail": f"{urlsplit(base).netloc} up · ollama {ver} · {config.VISION_MODEL}".strip()}
    except Exception as e:
        reason = getattr(e, "reason", None) or e.__class__.__name__
        return {"label": "Vision model", "ok": False,
                "detail": f"{urlsplit(base).netloc} unreachable ({str(reason)[:60]})"}


def check_disk() -> dict:
    root = config.LIBRARY_ROOT
    while not root.exists() and root != root.parent:
        root = root.parent
    u = shutil.disk_usage(root)
    pct = round(100 * u.used / u.total) if u.total else 0
    return {"label": "Disk", "ok": pct < DISK_WARN_PCT,
            "detail": f"{pct}% used · {u.free // 1024 ** 3} GB free"}


def check_vault() -> dict:
    # Read-only probe: vault.is_mounted() creates folders in dir mode, and a
    # status check must never write. Never echo the mount path.
    if config.VAULT_MODE == "dir":
        ok = config.VAULT_MOUNT.is_dir()
        return {"label": "Vault", "ok": ok,
                "detail": "folder mode · available" if ok else "folder mode · missing"}
    ok = os.path.ismount(config.VAULT_MOUNT)
    return {"label": "Vault", "ok": ok,
            "detail": "encrypted volume mounted" if ok
            else "locked — flagged photos wait in review"}


def check_watchdog() -> dict:
    running = False
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    if b"mvault-watchdog" in fh.read(4096):
                        running = True
                        break
            except OSError:
                continue
    except OSError:
        return {"label": "Watchdog", "ok": None, "detail": "can't inspect processes here"}
    return {"label": "Watchdog", "ok": True if running else None,
            "detail": "running" if running else "not running (optional)"}


def check_access() -> dict:
    from . import auth, tls
    pin, cert = auth.pin_configured(), tls.configured()
    return {"label": "PIN + HTTPS", "ok": pin and cert,
            "detail": f"PIN {'set' if pin else 'NOT set'} · cert {'present' if cert else 'missing'}"}


def run_checks(db_path, deadline_s: float = DEADLINE_S) -> list[dict]:
    probes = [
        ("Library DB", lambda: check_db(db_path)),
        ("Pipeline", lambda: check_pipeline(db_path)),
        ("Vision model", check_vision),
        ("Disk", check_disk),
        ("Vault", check_vault),
        ("Watchdog", check_watchdog),
        ("PIN + HTTPS", check_access),
    ]
    results: list[dict | None] = [None] * len(probes)

    def run(i, label, fn):
        try:
            results[i] = fn()
        except Exception as e:
            results[i] = {"label": label, "ok": False,
                          "detail": f"{e.__class__.__name__}: {str(e)[:80]}"}

    threads = []
    for i, (label, fn) in enumerate(probes):
        t = threading.Thread(target=run, args=(i, label, fn), daemon=True)
        t.start()
        threads.append(t)
    end = time.monotonic() + deadline_s
    for t in threads:
        t.join(max(0.0, end - time.monotonic()))
    out = []
    for (label, _), r in zip(probes, results):
        out.append(r or {"label": label, "ok": None, "detail": "timed out"})
    # detail text passes the same scrubber as the log (defence in depth)
    from .debuglog import redact
    for c in out:
        c["detail"] = redact(str(c.get("detail", "")))[:200]
    return out
