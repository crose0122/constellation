"""Family-PIN access control, driven over real HTTP (V2 CP1, spec A6).

Pins down: every route is classified (fail-closed 404 otherwise); display
surfaces stay open with no PIN; every private read and every mutation is
refused without a session; mutations also need the CSRF header; the PIN only
works over TLS (and a LAN client can't fake TLS with a header); lockout; the
PIN is never stored in the clear; share stays off by default.

Run: python3 -m pytest tests/test_auth.py -q
"""

import http.client
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memoryvault import config, db  # noqa: E402
from memoryvault.constellation import auth  # noqa: E402
from memoryvault.constellation import server as srv  # noqa: E402

SERVER_SRC = Path(srv.__file__).read_text()
PIN = "4827"
TLS = {"X-Forwarded-Proto": "https"}


def _served_paths() -> set[str]:
    """Every path the handler answers, read from the dispatcher itself so a
    new endpoint can't slip past the classification table."""
    exact = set(re.findall(r'url\.path == "([^"]+)"', SERVER_SRC))
    for grp in re.findall(r"url\.path in \(([^)]*)\)", SERVER_SRC):
        exact |= set(re.findall(r'"([^"]+)"', grp))
    exact |= set(re.findall(r'path == "([^"]+)"', SERVER_SRC))
    prefixes = set(re.findall(r'url\.path\.startswith\("([^"]+)"\)', SERVER_SRC))
    return exact | {p + "x" for p in prefixes}


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="mv-auth-"))
        cls._saved = (config.LIBRARY_ROOT, config.DB_PATH)
        config.LIBRARY_ROOT = cls.tmp / "library"
        config.DB_PATH = config.LIBRARY_ROOT / "photos.db"
        db.init(config.DB_PATH).close()
        os.environ["MEMORYVAULT_PIN_FILE"] = str(cls.tmp / "pin.json")
        os.environ.pop("MEMORYVAULT_REQUIRE_TLS", None)
        os.environ.pop("MEMORYVAULT_SHARE_ENABLED", None)
        cls._saved_db = srv.Handler.condb
        srv.Handler.condb = srv.ConstellationDB(config.DB_PATH)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
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

    def req(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        h = dict(headers or {})
        if data is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        payload = r.read()
        return r.status, dict(r.getheaders()), payload, r.msg.get_all("Set-Cookie") or []

    def login(self, pin=PIN, headers=TLS):
        status, _, _, cookies = self.req("POST", "/api/auth/login", headers, {"pin": pin})
        jar = {}
        for c in cookies:
            k, v = c.split(";", 1)[0].split("=", 1)
            jar[k] = v
        return status, jar

    @staticmethod
    def cookie_header(jar):
        return "; ".join(f"{k}={v}" for k, v in jar.items())


class ClassificationTest(_Base):
    def test_every_served_route_is_classified(self):
        missing = sorted(p for p in _served_paths() if auth.classify(p) is None)
        self.assertEqual(missing, [], f"unclassified routes (unreachable until classified): {missing}")

    def test_unknown_path_is_404_before_any_handler(self):
        status, *_ = self.req("GET", "/api/definitely-new-endpoint")
        self.assertEqual(status, 404)

    def test_unclassified_route_is_unreachable_even_if_a_handler_exists(self):
        # Pretend a developer added a handler branch but forgot the table:
        # the gate must stop it before the handler runs.
        saved = dict(auth.ROUTES)
        del auth.ROUTES["/api/purge"]
        try:
            status, *_ = self.req("GET", "/api/purge?bin=Trash&confirm=DELETE", TLS)
            self.assertEqual(status, 404)
        finally:
            auth.ROUTES.clear()
            auth.ROUTES.update(saved)


class OpenSurfacesTest(_Base):
    def test_picture_frame_needs_no_pin(self):
        for path in ("/", "/wall", "/ambient", "/memories", "/gallery", "/api/progress",
                     "/api/categories", "/api/gallery", "/login", "/static/pin.js"):
            status, *_ = self.req("GET", path)
            self.assertNotIn(status, (401, 403), path)

    def test_favicon_is_explicitly_open_and_serves_the_brand_png(self):
        expected = (srv.STATIC_DIR / "icon-192.png").read_bytes()
        self.assertEqual(auth.classify("/favicon.ico"), auth.OPEN)
        status, headers, body, _ = self.req("GET", "/favicon.ico")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(body, expected)
        self.assertEqual(
            __import__("hashlib").sha256(body).hexdigest(),
            "2d0b55c60dffab841138bb43159ef244543ff0238d5ea691709aa61eceb12f58",
        )

    def test_login_page_declares_the_brand_favicon(self):
        status, _, body, _ = self.req("GET", "/login")
        self.assertEqual(status, 200)
        self.assertIn(b'<link rel="icon" href="/static/icon-192.png">', body)


class PrivateSurfacesTest(_Base):
    # Written out by hand ON PURPOSE. Deriving this list from auth.ROUTES
    # would let a route silently drop out of the test the moment someone
    # reclassifies it as OPEN (mutation-proven: /api/purge -> OPEN passed).
    PRIVATE_READS = ["/people", "/person", "/curation", "/api/people", "/api/person",
                     "/api/curation", "/api/purged", "/api/vaulted", "/face/abc.jpg"]
    WRITES = ["/api/people/notthem", "/api/people/removeall", "/api/people/label",
              "/api/purge", "/api/photo/share", "/api/photo/vault",
              "/api/photo/markdelete", "/api/photo/remove", "/api/curation/restore"]
    PRIVATE = PRIVATE_READS + WRITES

    def test_expected_classification_is_exact(self):
        for p in self.PRIVATE_READS:
            self.assertEqual(auth.classify(p), auth.PIN, p)
        for p in self.WRITES:
            self.assertEqual(auth.classify(p), auth.PIN_WRITE, p)

    def test_every_private_route_refuses_without_session(self):
        for path in self.PRIVATE:
            status, *_ = self.req("GET", path + "?id=1&label=x&mode=all&confirm=DELETE&bin=Trash",
                                  headers={**TLS, "Accept": "application/json"})
            self.assertEqual(status, 401, path)

    def test_html_page_redirects_to_login(self):
        status, headers, *_ = self.req("GET", "/curation", {**TLS, "Accept": "text/html"})
        self.assertEqual(status, 303)
        self.assertTrue(headers["Location"].startswith("/login?next=/curation"))

    def test_lan_client_cannot_fake_tls_header(self):
        # The test server's peer IS loopback, so emulate a LAN peer directly.
        class H:
            def get(self, k, d=""):
                return {"X-Forwarded-Proto": "https"}.get(k, d)
        self.assertFalse(auth.request_is_tls("192.0.2.10", H()))
        self.assertTrue(auth.request_is_tls("127.0.0.1", H()))

    def test_plain_http_refused_for_pin_and_login(self):
        status, *_ = self.req("GET", "/api/curation")
        self.assertEqual(status, 403)
        status, _ = self.login(headers={})
        self.assertEqual(status, 403)


class SessionTest(_Base):
    def test_login_then_private_read_and_csrf_guarded_write(self):
        status, jar = self.login()
        self.assertEqual(status, 200)
        self.assertIn(auth.SESSION_COOKIE, jar)
        ck = {"Cookie": self.cookie_header(jar), **TLS}
        status, *_ = self.req("GET", "/api/curation", ck)
        self.assertEqual(status, 200)
        # write without CSRF header -> refused, even with a valid session
        status, *_ = self.req("GET", "/api/curation/restore?id=999999", ck)
        self.assertEqual(status, 403)
        # wrong CSRF -> refused
        status, *_ = self.req("GET", "/api/curation/restore?id=999999",
                              {**ck, auth.CSRF_HEADER: "nope"})
        self.assertEqual(status, 403)
        # right CSRF -> reaches the handler
        status, *_ = self.req("GET", "/api/curation/restore?id=999999",
                              {**ck, auth.CSRF_HEADER: jar[auth.CSRF_COOKIE]})
        self.assertNotIn(status, (401, 403))

    def test_cookie_flags(self):
        _, _, _, cookies = self.req("POST", "/api/auth/login", TLS, {"pin": PIN})
        sess = next(c for c in cookies if c.startswith(auth.SESSION_COOKIE + "="))
        for flag in ("HttpOnly", "SameSite=Strict", "Secure", "Path=/"):
            self.assertIn(flag, sess)

    def test_logout_revokes(self):
        _, jar = self.login()
        ck = {"Cookie": self.cookie_header(jar), **TLS}
        self.req("POST", "/api/auth/logout", ck)
        status, *_ = self.req("GET", "/api/curation", ck)
        self.assertEqual(status, 401)

    def test_changing_pin_signs_everyone_out(self):
        _, jar = self.login()
        auth.set_pin("9999")
        status, *_ = self.req("GET", "/api/curation", {"Cookie": self.cookie_header(jar), **TLS})
        self.assertEqual(status, 401)

    def test_expiry(self):
        token, _ = auth.SESSIONS.create(now=1000.0)
        self.assertIsNotNone(auth.SESSIONS.get(token, now=1000.0 + auth.IDLE_S - 1))
        self.assertIsNone(auth.SESSIONS.get(token, now=1000.0 + auth.IDLE_S * 3))
        token, _ = auth.SESSIONS.create(now=0.0)
        t, got = 0.0, None
        while t < auth.ABSOLUTE_S:  # keep it busy, still dies at the absolute cap
            t += auth.IDLE_S / 2
            got = auth.SESSIONS.get(token, now=t)
        self.assertIsNone(got)


class LockoutTest(_Base):
    def test_wrong_pins_lock_the_client(self):
        for _ in range(auth.FREE_ATTEMPTS):
            status, _ = self.login(pin="0000")
            self.assertEqual(status, 401)
        status, _ = self.login(pin="0000")
        self.assertEqual(status, 429)
        status, _ = self.login(pin=PIN)  # even the right PIN waits
        self.assertEqual(status, 429)

    def test_backoff_grows_and_caps(self):
        lk = auth.Lockout()
        waits = []
        for i in range(12):
            lk.fail("c", now=0.0)
            waits.append(lk.retry_after("c", now=0.0))
        self.assertEqual(waits[:auth.FREE_ATTEMPTS - 1], [0] * (auth.FREE_ATTEMPTS - 1))
        self.assertTrue(all(b >= a for a, b in zip(waits, waits[1:])))
        self.assertLessEqual(max(waits), auth.MAX_LOCK_S)


class StorageTest(_Base):
    def test_pin_never_stored_in_clear(self):
        raw = Path(os.environ["MEMORYVAULT_PIN_FILE"]).read_text()
        self.assertNotIn(PIN, raw)
        self.assertEqual(oct(Path(os.environ["MEMORYVAULT_PIN_FILE"]).stat().st_mode & 0o777), "0o600")

    def test_short_pin_rejected(self):
        with self.assertRaises(ValueError):
            auth.set_pin("12")

    def test_wrong_pin_fails_right_pin_passes(self):
        self.assertTrue(auth.check_pin(PIN))
        self.assertFalse(auth.check_pin(PIN + "0"))
        self.assertFalse(auth.check_pin(""))


class ShareTest(_Base):
    def test_share_off_by_default_even_signed_in(self):
        _, jar = self.login()
        status, *_ = self.req("GET", "/api/photo/share?id=1&to=a@example.com",
                              {"Cookie": self.cookie_header(jar), **TLS,
                               auth.CSRF_HEADER: jar[auth.CSRF_COOKIE]})
        self.assertEqual(status, 404)

    def test_share_still_needs_pin_when_enabled(self):
        os.environ["MEMORYVAULT_SHARE_ENABLED"] = "1"
        try:
            status, *_ = self.req("GET", "/api/photo/share?id=1&to=a@example.com", TLS)
            self.assertEqual(status, 401)
        finally:
            os.environ.pop("MEMORYVAULT_SHARE_ENABLED", None)


class TlsSwitchTest(_Base):
    def test_typo_keeps_tls_required(self):
        os.environ["MEMORYVAULT_REQUIRE_TLS"] = "off"
        try:
            self.assertTrue(auth.require_tls())
        finally:
            os.environ.pop("MEMORYVAULT_REQUIRE_TLS", None)


if __name__ == "__main__":
    unittest.main()
