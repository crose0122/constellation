"""Family-PIN access control for the Constellation server (V2 CP1, spec A6).

Open surfaces (no login): the wall, Memories, ambient and the browse pages a
picture frame needs — "a picture frame never asks for a password".
PIN surfaces: curation, people, vault tools, purge, sharing, anything that
changes the library.

Every route the server answers MUST appear in ROUTES. ``classify()`` returns
None for anything else and the server answers 404 before a handler runs, so a
new endpoint is unreachable until someone decides, in this table, who may
reach it. Fail closed by construction, not by remembering.

Mutations additionally need the CSRF header (double-submit of the session's
token). The server sends no CORS headers, so a cross-site page can't attach a
custom header without a preflight that fails — an <img src="/api/purge?...">
from another site can no longer delete anything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

OPEN, PIN, PIN_WRITE = "open", "pin", "pin_write"

# exact paths
ROUTES: dict[str, str] = {
    # display surfaces — open
    "/": OPEN, "/ambient": OPEN, "/memories": OPEN, "/wall": OPEN,
    "/menu": OPEN, "/home": OPEN, "/gallery": OPEN, "/node": OPEN,
    "/progress": OPEN, "/manifest.json": OPEN, "/sw.js": OPEN,
    "/login": OPEN, "/api/dbg": OPEN, "/ca.pem": OPEN,
    "/api/progress": OPEN, "/api/categories": OPEN, "/api/index": OPEN,
    "/api/category": OPEN, "/api/catphoto": OPEN, "/api/family": OPEN,
    "/api/intersect": OPEN, "/api/start": OPEN, "/api/neighborhood": OPEN,
    "/api/photo": OPEN, "/api/gallery": OPEN,
    "/api/auth/status": OPEN, "/api/auth/login": OPEN, "/api/auth/logout": OPEN,
    # private reads — PIN
    "/people": PIN, "/person": PIN, "/curation": PIN,
    "/api/people": PIN, "/api/person": PIN, "/api/curation": PIN,
    "/api/purged": PIN, "/api/vaulted": PIN, "/api/vault/folders": PIN,
    # anything that changes the library — PIN + CSRF
    "/api/people/notthem": PIN_WRITE, "/api/people/removeall": PIN_WRITE,
    "/api/people/label": PIN_WRITE, "/api/purge": PIN_WRITE,
    "/api/photo/share": PIN_WRITE, "/api/photo/vault": PIN_WRITE,
    "/api/photo/markdelete": PIN_WRITE, "/api/photo/remove": PIN_WRITE,
    "/api/curation/restore": PIN_WRITE,
}
# prefixes (content-addressed media)
PREFIXES: tuple[tuple[str, str], ...] = (
    ("/static/", OPEN), ("/thumb/", OPEN), ("/display/", OPEN),
    ("/video/", OPEN),
    ("/face/", PIN),  # face crops are people data
)

# a dry-run of removeall is a read, but it lives on the same path; it still
# needs the PIN, and the CSRF header costs the UI nothing, so keep it simple.


def classify(path: str) -> str | None:
    if path in ROUTES:
        return ROUTES[path]
    for prefix, cls in PREFIXES:
        if path.startswith(prefix):
            return cls
    return None


# ── PIN storage ───────────────────────────────────────────────────────────

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
MIN_PIN_LEN = 4
MAX_PIN_LEN = 64


def pin_path() -> Path:
    override = os.environ.get("MEMORYVAULT_PIN_FILE")
    return Path(override) if override else config.LIBRARY_ROOT / ".auth" / "pin.json"


def _derive(pin: str, salt: bytes, params: dict) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=params["n"], r=params["r"],
                          p=params["p"], dklen=params["dklen"],
                          maxmem=128 * params["n"] * params["r"] * 2)


def set_pin(pin: str) -> None:
    pin = (pin or "").strip()
    if not (MIN_PIN_LEN <= len(pin) <= MAX_PIN_LEN):
        raise ValueError(f"PIN must be {MIN_PIN_LEN}-{MAX_PIN_LEN} characters")
    salt = secrets.token_bytes(16)
    record = {"v": 1, "kdf": "scrypt", **_SCRYPT, "salt": salt.hex(),
              "hash": _derive(pin, salt, _SCRYPT).hex()}
    p = pin_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(record, fh)
    os.replace(tmp, p)
    os.chmod(p, 0o600)
    SESSIONS.clear()  # changing the PIN signs everyone out


def pin_configured() -> bool:
    return pin_path().is_file()


def check_pin(pin: str) -> bool:
    try:
        record = json.loads(pin_path().read_text())
        params = {k: record[k] for k in ("n", "r", "p", "dklen")}
        want = bytes.fromhex(record["hash"])
        got = _derive(str(pin or ""), bytes.fromhex(record["salt"]), params)
    except (OSError, ValueError, KeyError):
        return False
    return hmac.compare_digest(got, want)


# ── lockout ───────────────────────────────────────────────────────────────

FREE_ATTEMPTS = 5
BASE_LOCK_S = 30
MAX_LOCK_S = 15 * 60


@dataclass
class _Throttle:
    failures: int = 0
    locked_until: float = 0.0


class Lockout:
    def __init__(self):
        self._by_client: dict[str, _Throttle] = {}
        self._lock = threading.Lock()

    def retry_after(self, client: str, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            t = self._by_client.get(client)
            return max(0, int(t.locked_until - now + 0.999)) if t else 0

    def fail(self, client: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            t = self._by_client.setdefault(client, _Throttle())
            t.failures += 1
            if t.failures >= FREE_ATTEMPTS:
                over = t.failures - FREE_ATTEMPTS
                t.locked_until = now + min(MAX_LOCK_S, BASE_LOCK_S * (2 ** over))

    def success(self, client: str) -> None:
        with self._lock:
            self._by_client.pop(client, None)

    def clear(self):
        with self._lock:
            self._by_client.clear()


LOCKOUT = Lockout()

# ── sessions ──────────────────────────────────────────────────────────────

SESSION_COOKIE = "mv_session"
CSRF_COOKIE = "mv_csrf"
CSRF_HEADER = "X-Constellation-CSRF"
ABSOLUTE_S = 8 * 3600
IDLE_S = 60 * 60


@dataclass
class Session:
    csrf: str
    created: float
    seen: float = field(default=0.0)


class Sessions:
    """Server-side, in memory. Only the SHA-256 of the cookie is kept, so a
    memory dump doesn't hand out live sessions. A restart signs everyone out,
    which for a family photo server is the right trade."""

    def __init__(self):
        self._s: dict[str, Session] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create(self, now: float | None = None) -> tuple[str, str]:
        now = time.time() if now is None else now
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with self._lock:
            self._s[self._key(token)] = Session(csrf=csrf, created=now, seen=now)
        return token, csrf

    def get(self, token: str | None, now: float | None = None) -> Session | None:
        if not token:
            return None
        now = time.time() if now is None else now
        k = self._key(token)
        with self._lock:
            s = self._s.get(k)
            if not s:
                return None
            if now - s.created >= ABSOLUTE_S or now - s.seen >= IDLE_S:
                self._s.pop(k, None)
                return None
            s.seen = now
            return s

    def revoke(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._s.pop(self._key(token), None)

    def clear(self):
        with self._lock:
            self._s.clear()


SESSIONS = Sessions()


# ── transport ─────────────────────────────────────────────────────────────

def require_tls() -> bool:
    # default ON: the PIN must never cross Wi-Fi in the clear (spec A6).
    # Anything other than an explicit "0" keeps it on — a typo fails closed.
    return os.environ.get("MEMORYVAULT_REQUIRE_TLS", "1").strip() != "0"


def request_is_tls(peer_ip: str, headers) -> bool:
    """True when the request reached us over HTTPS. The server itself speaks
    plain HTTP behind the family-CA TLS proxy (Caddy) on the same box, so
    X-Forwarded-Proto is honoured ONLY from a loopback peer — a LAN client
    talking straight to :8484 cannot claim TLS by sending the header."""
    if peer_ip not in ("127.0.0.1", "::1"):
        return False
    return (headers.get("X-Forwarded-Proto", "") or "").strip().lower() == "https"


def share_enabled() -> bool:
    # Product decision (V2 build plan Q4): email sharing stays, PIN-gated, OFF by default.
    return os.environ.get("MEMORYVAULT_SHARE_ENABLED", "0").strip() == "1"


def parse_cookies(header: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (header or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out
