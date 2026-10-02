"""Debug Console log ring (fleet debug-console contract v1).

The server runs headless under systemd; this keeps the last few hundred lines
it printed or logged in memory so /api/debug/state (PIN-gated) can show them in
the in-page Debug Console (static/debug.js, Ctrl+Shift+D).

install_tap() — once per process — adds a logging.Handler on the root logger
AND tees sys.stdout / sys.stderr, so bare print() lines land too. The tee never
raises and always writes to the original stream first: a broken buffer must
never cost the family their journal.

Privacy: every line is redacted BEFORE it enters the ring — secrets
(token/key/secret/password/pin/authorization = value, bearer strings) and any
path inside the encrypted vault (the whole path becomes "[vault path]"); the
library root is shortened to "<library>". Entries are status/log text only.
"""

from __future__ import annotations

import io
import logging
import re
import sys
import threading
from collections import deque
from datetime import datetime, timedelta, timezone

MAX_ENTRIES = 600
MAX_MSG = 2000

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# contract pattern, widened to "pass" (SMTP_PASS, passphrase) and the bare
# word "pin" (the family PIN is this product's secret)
_SECRET = re.compile(
    r"(?i)\b([\w.-]*(?:token|key|secret|pass|authorization)[\w.-]*"
    r"|pin)([\"']?\s*[=:]\s*)(?:bearer\s+)?(\"[^\"]*\"|'[^']*'|\S+)")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}")

_lock = threading.Lock()
_entries: deque = deque(maxlen=MAX_ENTRIES)
_last_t: datetime | None = None
_tapped = False


def _vault_prefixes() -> list[str]:
    try:
        from .. import config
        out = []
        for p in (config.VAULT_MOUNT, config.VAULT_IMG):
            s = str(p).rstrip("/")
            if len(s) > 1:
                out.append(s)
        return out
    except Exception:
        return []


def _library_prefix() -> str | None:
    try:
        from .. import config
        s = str(config.LIBRARY_ROOT).rstrip("/")
        return s if len(s) > 1 else None
    except Exception:
        return None


def redact(msg: str) -> str:
    """Scrub one line. Vault paths first (whole path, not just the prefix:
    the filenames inside the vault are themselves private)."""
    for pre in sorted(_vault_prefixes(), key=len, reverse=True):
        msg = re.sub(re.escape(pre) + r"(?:[/\\][^\s'\"),\]]*)?", "[vault path]", msg)
    msg = re.sub(r"/dev/mapper/\S+", "[vault device]", msg)
    lib = _library_prefix()
    if lib:
        msg = msg.replace(lib, "<library>")
    msg = _BEARER.sub("Bearer [redacted]", msg)
    msg = _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", msg)
    return msg


def _next_t() -> datetime:
    # strictly increasing timestamps, so ?since=<last t> never drops a line
    # that was logged in the same microsecond as the previous one
    global _last_t
    t = datetime.now(timezone.utc)
    if _last_t is not None and t <= _last_t:
        t = _last_t + timedelta(microseconds=1)
    _last_t = t
    return t


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="microseconds").replace("+00:00", "Z")


def push(level: str, msg) -> None:
    """Add one entry. Never raises."""
    try:
        if level not in ("log", "warn", "error"):
            level = "log"
        text = _ANSI.sub("", str(msg)).rstrip("\r\n")
        if not text.strip():
            return
        text = redact(text)[:MAX_MSG]
        with _lock:
            _entries.append({"t": _iso(_next_t()), "level": level, "msg": text})
    except Exception:
        pass


def entries(since: str | None = None) -> list[dict]:
    with _lock:
        snap = list(_entries)
    if not since:
        return snap
    return [e for e in snap if e["t"] > since]


def clear() -> None:
    with _lock:
        _entries.clear()


class _Handler(logging.Handler):
    def emit(self, record):
        try:
            lvl = ("error" if record.levelno >= logging.ERROR
                   else "warn" if record.levelno >= logging.WARNING else "log")
            push(lvl, self.format(record))
        except Exception:
            pass


_ERRORISH = re.compile(r"Traceback|Error\b|Exception\b|\] 5\d\d ")


class _Tee(io.TextIOBase):
    """Write-through wrapper: original stream first, ring second."""

    def __init__(self, orig, stream_name: str):
        self._orig = orig
        self._name = stream_name
        self._buf = ""
        self._in_tb = False
        self._blk = threading.Lock()

    def write(self, s):
        n = self._orig.write(s)
        try:
            with self._blk:
                self._buf += s
                lines = self._buf.split("\n")
                self._buf = lines.pop()
                if len(self._buf) > MAX_MSG * 4:   # runaway line with no newline
                    lines.append(self._buf)
                    self._buf = ""
            for line in lines:
                if self._name == "stderr":
                    # a traceback stays red through its indented frames and
                    # the final "SomeError: ..." line
                    if line.startswith("Traceback"):
                        self._in_tb = True
                    lvl = "error" if (self._in_tb or _ERRORISH.search(line)) else "warn"
                    if self._in_tb and line and not line[:1].isspace() \
                            and not line.startswith("Traceback"):
                        self._in_tb = False
                    push(lvl, line)
                else:
                    push("log", line)
        except Exception:
            pass
        return n

    def flush(self):
        try:
            self._orig.flush()
        except Exception:
            pass

    def __getattr__(self, name):          # fileno, isatty, encoding, buffer...
        return getattr(self._orig, name)

    def writable(self):
        return True


def install_tap() -> None:
    """Idempotent: mirror root logging + stdout/stderr into the ring."""
    global _tapped
    with _lock:
        if _tapped:
            return
        _tapped = True
    try:
        h = _Handler()
        h.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        logging.getLogger().addHandler(h)
        if not isinstance(sys.stdout, _Tee):
            sys.stdout = _Tee(sys.stdout, "stdout")
        if not isinstance(sys.stderr, _Tee):
            sys.stderr = _Tee(sys.stderr, "stderr")
    except Exception:
        pass
    push("log", "[debug] console tap installed — server output mirrors to the "
                "Debug Console (Ctrl+Shift+D)")
