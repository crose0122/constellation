"""Egress guard: Constellation must never talk to anything off the LAN.

Build-plan invariant 1 (CONSTELLATION-V2-BUILD-PLAN.md §2). Two halves:

1. Runtime: a full pipeline pass — discover → ingest → dedup → screen → tag
   (through the REAL vision HTTP client) → edges — plus a spin through the
   web server's pages, with every socket connect and every DNS lookup in the
   process recorded. Any destination that isn't loopback or a private-LAN
   address fails the test. The vision "model" is a fake Ollama on 127.0.0.1,
   so the real request path is exercised without a GPU.

2. Static: no page the server ships may pull a script, stylesheet, font or
   image from the internet — a CDN <script> would leak the family's IP and
   browsing to a third party on every wall refresh.

Screening's pass-1 classifier is injected (weights aren't in CI); its loader
is covered by the static half not being a network path. Honest limitation,
recorded in the build plan.

Run: python3 -m pytest tests/test_egress.py -q
"""

import ipaddress
import json
import re
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from memoryvault import config, db  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "memoryvault" / "constellation" / "static"


def _is_lan(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # a hostname that needs resolving off-box is egress
    return ip.is_loopback or ip.is_private or ip.is_link_local


class EgressRecorder:
    """Records every outbound connect/DNS lookup in this process and blocks
    anything that isn't LAN, so a leak fails fast instead of reaching out."""

    def __init__(self):
        self.seen, self.blocked = [], []

    def __enter__(self):
        self._connect = socket.socket.connect
        self._connect_ex = socket.socket.connect_ex
        self._getaddrinfo = socket.getaddrinfo
        rec = self

        def _check(addr):
            host = addr[0] if isinstance(addr, tuple) else str(addr)
            rec.seen.append(host)
            if isinstance(addr, tuple) and not _is_lan(str(host)):
                rec.blocked.append(host)
                raise ConnectionRefusedError(f"egress blocked by test: {host}")

        def connect(sock, addr):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                _check(addr)
            return rec._connect(sock, addr)

        def connect_ex(sock, addr):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                _check(addr)
            return rec._connect_ex(sock, addr)

        def getaddrinfo(host, *a, **kw):
            h = host.decode() if isinstance(host, bytes) else str(host)
            if h and not _is_lan(h):
                rec.blocked.append(h)
                raise socket.gaierror(f"DNS lookup blocked by test: {h}")
            return rec._getaddrinfo(host, *a, **kw)

        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex
        socket.getaddrinfo = getaddrinfo
        return self

    def __exit__(self, *exc):
        socket.socket.connect = self._connect
        socket.socket.connect_ex = self._connect_ex
        socket.getaddrinfo = self._getaddrinfo
        return False


class _FakeOllama(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        assert body.get("images"), "vision request must carry the image"
        _FakeOllama.calls += 1
        reply = {"response": json.dumps(
            {"people": ["Alex"], "occasion": "Birthday", "year": "2024"})}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def _photo(path: Path, seed: int):
    img = Image.new("RGB", (400, 300), (40 + seed * 13 % 200, 80, 120))
    d = ImageDraw.Draw(img)
    d.ellipse([50 + seed * 5, 50, 200 + seed * 5, 200], fill=(220, 180, 60))
    img.save(path, "JPEG", quality=90)


class RuntimeEgressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mv-egress-"))
        self.src = self.tmp / "source"
        self.src.mkdir()
        self._saved = {k: getattr(config, k) for k in (
            "LIBRARY_ROOT", "DB_PATH", "VAULT_MOUNT", "VAULT_MODE", "OLLAMA_URL")}
        config.LIBRARY_ROOT = self.tmp / "library"
        config.DB_PATH = config.LIBRARY_ROOT / "photos.db"
        config.VAULT_MODE = "dir"
        config.VAULT_MOUNT = self.tmp / "vault"
        self.conn = db.init(config.DB_PATH)
        for i in range(4):
            _photo(self.src / f"p{i}.jpg", seed=i)

        self.ollama = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
        threading.Thread(target=self.ollama.serve_forever, daemon=True).start()
        config.OLLAMA_URL = f"http://127.0.0.1:{self.ollama.server_address[1]}/api/generate"
        _FakeOllama.calls = 0

    def tearDown(self):
        self.ollama.shutdown()
        for k, v in self._saved.items():
            setattr(config, k, v)

    def _pipeline(self):
        from memoryvault.dedup import dedup
        from memoryvault.discover import discover
        from memoryvault.edges import compute_edges
        from memoryvault.ingest import ingest
        from memoryvault.screen import screen
        from memoryvault.tag import tag

        discover(self.conn, self.src)
        ingest(self.conn)
        dedup(self.conn)
        screen(self.conn, score_fn=lambda p: 0.01, confirm_fn=lambda p: False)
        tag(self.conn)  # REAL call_vision -> vision_http -> HTTP
        compute_edges(self.conn)

    def test_full_pipeline_stays_on_the_lan(self):
        with EgressRecorder() as rec:
            self._pipeline()
        self.assertEqual(rec.blocked, [], f"off-LAN egress attempted: {rec.blocked}")
        self.assertGreater(_FakeOllama.calls, 0, "vision path was not exercised")
        self.assertTrue(rec.seen, "recorder saw no traffic; guard is not wired")

    def test_web_pages_stay_on_the_lan(self):
        from memoryvault.constellation.server import ConstellationDB, Handler

        saved = getattr(Handler, "condb", None)
        Handler.condb = ConstellationDB(config.DB_PATH)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        try:
            with EgressRecorder() as rec:
                for path in ("/", "/wall", "/gallery", "/menu", "/progress",
                             "/api/categories"):
                    try:
                        urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10).read()
                    except urllib.error.HTTPError:
                        pass  # a 4xx/5xx is fine here; we're watching the wire
        finally:
            httpd.shutdown()
            Handler.condb = saved
        self.assertEqual(rec.blocked, [], f"off-LAN egress attempted: {rec.blocked}")

    def test_guard_actually_blocks_an_external_host(self):
        # The guard itself must bite, or the two tests above prove nothing.
        config.OLLAMA_URL = "http://ollama.example.com:11434/api/generate"
        with EgressRecorder() as rec:
            with self.assertRaises(Exception):
                from memoryvault.vision_http import post_vision_text
                post_vision_text({"images": ["x"]}, timeout=5)
        self.assertIn("ollama.example.com", rec.blocked)


_EXTERNAL_REF = re.compile(
    r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//(?!127\.0\.0\.1|localhost)[^"']+"""
    r"""|url\(\s*["']?\s*(?:https?:)?//(?!127\.0\.0\.1|localhost)[^)]+\)"""
    r"""|@import\s+["']?(?:https?:)?//"""
    r"""|\bimport\s[^;]*?from\s+["']https?://"""
    r"""|\bfetch\(\s*["']https?://""",
    re.I,
)


class StaticEgressTest(unittest.TestCase):
    def test_shipped_pages_load_nothing_from_the_internet(self):
        offenders = []
        files = [p for p in STATIC.rglob("*")
                 if p.suffix in (".html", ".js", ".css", ".json")]
        self.assertTrue(files, "no static files found; path is wrong")
        for p in files:
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if _EXTERNAL_REF.search(line):
                    offenders.append(f"{p.name}:{i}: {line.strip()[:100]}")
        self.assertEqual(offenders, [], "external resource references:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
