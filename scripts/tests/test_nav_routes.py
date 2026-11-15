"""Task #388: no page may 500 or trap a person.

Pins down, over real HTTP:
- `/` (and every page shell) answers 200 even when the host decodes text with
  the Windows ANSI code page — the root cause of the Windows `/` 500 was
  `index.html.read_text()` without an encoding (cp1252 can't decode 0x90).
- page shells and /static assets are served before the DB is opened, so a
  missing library gives a page (whose API calls then report it), not a 503.
- every HTML page carries the injected way-home control (static/nav.js).
- a browser asking for a private page over plain HTTP with nowhere to redirect
  gets a readable HTML page with Home/Menu links; API callers still get JSON.
- signed in over TLS, /people and /curation answer 200 with the control.

Run: python3 -m pytest tests/test_nav_routes.py -q
"""

import http.client
import json
import os
import pathlib
import re
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memoryvault import config, db  # noqa: E402
from memoryvault.constellation import auth  # noqa: E402
from memoryvault.constellation import server as srv  # noqa: E402

PIN = "6142"
TLS = {"X-Forwarded-Proto": "https"}
HTML = {"Accept": "text/html,application/xhtml+xml"}
OPEN_PAGES = [p for p in srv.PAGES if auth.classify(p) == auth.OPEN]
PIN_PAGES = [p for p in srv.PAGES if auth.classify(p) == auth.PIN]

_real_read_text = pathlib.Path.read_text


def _windows_read_text(self, encoding=None, errors=None, **kw):
    """Path.read_text as a Windows (cp1252) host behaves with no encoding."""
    return _real_read_text(self, encoding=encoding or "cp1252", errors=errors, **kw)


class _Server(unittest.TestCase):
    db_present = True

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="mv-nav-"))
        cls._saved = (config.LIBRARY_ROOT, config.DB_PATH)
        config.LIBRARY_ROOT = cls.tmp / "library"
        config.DB_PATH = config.LIBRARY_ROOT / "photos.db"
        if cls.db_present:
            db.init(config.DB_PATH).close()
        os.environ["MEMORYVAULT_PIN_FILE"] = str(cls.tmp / "pin.json")
        os.environ.pop("MEMORYVAULT_REQUIRE_TLS", None)
        cls._saved_db = srv.Handler.condb
        srv.Handler.condb = srv.ConstellationDB(config.DB_PATH)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        cls.httpd.is_tls = False
        cls.httpd.tls_port = None  # no family certificate: nowhere to redirect
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        srv.Handler.condb = cls._saved_db
        config.LIBRARY_ROOT, config.DB_PATH = cls._saved
        os.environ.pop("MEMORYVAULT_PIN_FILE", None)

    def setUp(self):
        auth.SESSIONS.clear()
        auth.LOCKOUT.clear()
        auth.set_pin(PIN)

    def req(self, path, headers=None, method="GET", body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        h = dict(headers or {})
        if data is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read(), r.msg.get_all("Set-Cookie") or []

    def signed_in(self):
        status, _, _, cookies = self.req("/api/auth/login", TLS, "POST", {"pin": PIN})
        self.assertEqual(status, 200)
        jar = dict(c.split(";", 1)[0].split("=", 1) for c in cookies)
        return {**TLS, **HTML, "Cookie": "; ".join(f"{k}={v}" for k, v in jar.items())}


class WindowsEncodingTest(_Server):
    def test_root_200_under_windows_ansi_decoding(self):
        with mock.patch.object(pathlib.Path, "read_text", _windows_read_text):
            for p in ("/", "/ambient", "/memories"):
                status, _, body, _ = self.req(p, HTML)
                self.assertEqual(status, 200, (p, body[:120]))
                self.assertIn(srv.NAV_TAG, body)

    def test_index_really_is_not_cp1252_decodable(self):
        # guards the regression test itself: if index.html ever became
        # cp1252-safe the test above would stop proving anything
        raw = (srv.STATIC_DIR / "index.html").read_bytes()
        with self.assertRaises(UnicodeDecodeError):
            raw.decode("cp1252")

    def test_server_never_reads_text_without_encoding(self):
        src = Path(srv.__file__).read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"\.read_text\(\)", src.split('"""')[0] +
                                    "".join(src.split('"""')[2::2])), [])


class PagesTest(_Server):
    def test_every_open_page_200_with_nav(self):
        for p in OPEN_PAGES:
            status, headers, body, _ = self.req(p, HTML)
            self.assertEqual(status, 200, p)
            self.assertIn("text/html", headers["Content-Type"])
            self.assertEqual(body.count(srv.NAV_TAG), 1, p)

    def test_nav_asset_served(self):
        status, headers, body, _ = self.req("/static/nav.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertIn(b'"/menu"', body)
        self.assertNotIn(b"innerHTML", body)

    def test_pin_pages_200_with_nav_when_signed_in(self):
        h = self.signed_in()
        for p in PIN_PAGES:
            status, _, body, _ = self.req(p, h)
            self.assertEqual(status, 200, p)
            self.assertIn(srv.NAV_TAG, body)

    def test_pin_pages_still_gated(self):
        for p in PIN_PAGES:
            status, headers, _, _ = self.req(p, {**TLS, **HTML})
            self.assertEqual(status, 303, p)
            self.assertTrue(headers["Location"].startswith("/login?next="))

    def test_plain_http_private_page_is_readable_html_with_way_home(self):
        for p in PIN_PAGES:
            status, headers, body, _ = self.req(p, HTML)
            self.assertEqual(status, 403, p)
            self.assertIn("text/html", headers["Content-Type"])
            self.assertIn(b'href="/menu"', body)
            self.assertIn(b'href="/"', body)
            self.assertIn(srv.NAV_TAG, body)

    def test_api_callers_still_get_json_denials(self):
        status, headers, body, _ = self.req("/people", {"Accept": "application/json"})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "https required")
        status, _, body, _ = self.req("/api/people", HTML)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "https required")


class MissingLibraryTest(_Server):
    db_present = False

    def test_pages_and_assets_do_not_need_the_db(self):
        self.assertFalse(config.DB_PATH.exists())
        for p in OPEN_PAGES + ["/static/nav.js", "/static/constellation.css"]:
            status, _, _, _ = self.req(p, HTML)
            self.assertEqual(status, 200, p)

    def test_api_still_reports_db_unavailable(self):
        status, _, body, _ = self.req("/api/progress")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"], "db unavailable")


if __name__ == "__main__":
    unittest.main()
