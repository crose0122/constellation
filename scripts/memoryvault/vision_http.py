"""The one place the pipeline talks to the vision server.

Why this module exists: a `tag --retag` shard sat wedged for 22 hours against
an ESTABLISHED-but-dead socket to the second GPU box, holding up every job
queued behind it — the nightly backlog, a video ingest, and a document sweep
that consequently never ran. Its `requests.post(..., timeout=120)` never
fired. The call sites were also duplicated across five modules, so any fix had
to be made five times.

Three independent defences, because the exact reason that timeout didn't fire
was never established and a guess is not a fix:

  1. TCP keepalive on the socket. A peer that disappears without sending
     FIN/RST — a VM powered off, a host that panicked, a firewall dropping
     state — leaves the local end ESTABLISHED indefinitely, which is exactly
     what `ss` showed. Keepalive makes the kernel probe and tear the
     connection down, turning a silent hang into a normal socket error.

  2. Separate connect and read timeouts. `requests`' scalar timeout is
     per-socket-operation, not a ceiling on the call: a server that dribbles
     bytes, or stalls mid-send once its receive window closes, can keep a
     request alive far past it.

  3. A hard wall-clock ceiling via SIGALRM. Whatever the socket does — even
     blocked in a syscall the timeout machinery never inspects — the alarm
     interrupts it. This is the backstop that makes the failure mode
     impossible rather than unlikely.

Failures are raised, never swallowed: every caller already records them
per-photo and moves on, which is the behaviour we want. The bug was never
that a call failed — it was that a call neither succeeded nor failed.
"""
from __future__ import annotations

import signal
import socket
import threading

import requests
from requests.adapters import HTTPAdapter

from . import config

# Connect should be near-instant on a LAN; generation is what takes time.
CONNECT_TIMEOUT = 10

# Ceiling for the whole call. Generous — it exists to break hangs, not to cut
# short a slow model — but ABSOLUTELY capped: deriving it only as a multiple of
# the caller's read timeout means a careless timeout=9999 buys a seven-hour
# ceiling, which is the very failure this module exists to prevent.
WALL_CLOCK_FACTOR = 2.5
WALL_CLOCK_FLOOR = 90
WALL_CLOCK_CAP = 900        # no single vision call may outlive 15 minutes


class VisionTimeout(RuntimeError):
    """The call exceeded its wall-clock ceiling and was interrupted."""


class _KeepaliveAdapter(HTTPAdapter):
    """Sockets that notice when the far end stops existing."""

    def init_poolmanager(self, *args, **kwargs):
        opts = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
        # Linux-only knobs; absent elsewhere, in which case the OS defaults
        # (typically 2 hours idle) still beat waiting forever.
        for name, value in (("TCP_KEEPIDLE", 30),    # probe after 30s quiet
                            ("TCP_KEEPINTVL", 10),   # then every 10s
                            ("TCP_KEEPCNT", 5)):     # give up after 5
            opt = getattr(socket, name, None)
            if opt is not None:
                opts.append((socket.IPPROTO_TCP, opt, value))
        kwargs["socket_options"] = opts
        return super().init_poolmanager(*args, **kwargs)


_session = None
_session_lock = threading.Lock()


def session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            adapter = _KeepaliveAdapter(pool_connections=4, pool_maxsize=4)
            s.mount("http://", adapter)
            s.mount("https://", adapter)
            _session = s
        return _session


class _Deadline:
    """SIGALRM ceiling. Main thread only — every pipeline stage is a plain
    single-threaded CLI process, and where that does not hold we degrade to
    the socket timeouts rather than failing."""

    def __init__(self, seconds: int):
        self.seconds = max(1, int(seconds))
        self.armed = False

    def __enter__(self):
        if threading.current_thread() is not threading.main_thread():
            return self
        try:
            self._prev = signal.signal(signal.SIGALRM, self._fire)
            signal.alarm(self.seconds)
            self.armed = True
        except (ValueError, AttributeError, OSError):
            self.armed = False      # no SIGALRM here; defences 1 and 2 stand
        return self

    def _fire(self, signum, frame):
        raise VisionTimeout(
            f"vision call exceeded {self.seconds}s wall clock — "
            f"server {config.OLLAMA_URL} stopped responding mid-request")

    def __exit__(self, *exc):
        if self.armed:
            signal.alarm(0)
            try:
                signal.signal(signal.SIGALRM, self._prev)
            except (ValueError, OSError):
                pass
        return False


def post_vision(payload: dict, *, timeout: int = 120, url: str | None = None):
    """POST to the vision server and return the decoded JSON body.

    Raises on any failure — including a hang, which now surfaces as
    VisionTimeout instead of stopping the pipeline forever.
    """
    target = url or config.OLLAMA_URL
    ceiling = min(WALL_CLOCK_CAP,
                  max(WALL_CLOCK_FLOOR, int(timeout * WALL_CLOCK_FACTOR)))
    with _Deadline(ceiling):
        resp = session().post(
            target, json=payload, timeout=(CONNECT_TIMEOUT, timeout))
        resp.raise_for_status()
        return resp.json()


def post_vision_text(payload: dict, **kw) -> str:
    """The common case: everything here wants the `response` string."""
    return post_vision(payload, **kw).get("response", "")
