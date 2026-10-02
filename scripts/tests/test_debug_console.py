"""Debug Console (fleet debug-console contract v1), driven over real HTTP.

Pins down: /api/debug/state is behind the family PIN (401 without a session,
403 over plain HTTP, never OPEN in the route table); the payload has the
contract schema; ?since= tails; planted secrets and vault paths are redacted
before they reach the ring; the endpoint stays under 3 s when the vision
server hangs and still answers when the DB is gone; every page carries the
overlay include; the stdout/stderr tee never breaks the original stream; and
a real `mvault constellation` process mirrors its own startup lines.

Run: python3 -m pytest tests/test_debug_console.py -q
"""

import http.client
import io
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memoryvault import config, db  # noqa: E402
from memoryvault.constellation import auth, debuglog  # noqa: E402
from memoryvault.constellation import server as srv  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent.parent
PIN = "5904"
TLS = {"X-Forwarded-Proto": "https", "Accept": "application/json"}


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="mv-debug-"))
        cls._saved = (config.LIBRARY_ROOT, config.DB_PATH, config.VAULT_MOUNT,
                      config.VAULT_IMG, config.VAULT_MODE, config.OLLAMA_URL)
        config.LIBRARY_ROOT = cls.tmp / "library"
        config.DB_PATH = config.LIBRARY_ROOT / "photos.db"
        config.VAULT_MOUNT = cls.tmp / "sealedvaultxyz"
        config.VAULT_IMG = cls.tmp / "sealedvault.img"
        config.VAULT_MODE = "luks"
        # nothing listens here: the vision probe must fail fast, not hang
        config.OLLAMA_URL = "http://127.0.0.1:9/api/generate"
        db.init(config.DB_PATH).close()
        os.environ["MEMORYVAULT_PIN_FILE"] = str(cls.tmp / "pin.json")
        os.environ.pop("MEMORYVAULT_REQUIRE_TLS", None)
        cls._saved_db = srv.Handler.condb
        srv.Handler.condb = srv.ConstellationDB(config.DB_PATH)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        srv.Handler.condb = cls._saved_db
        (config.LIBRARY_ROOT, config.DB_PATH, config.VAULT_MOUNT,
         config.VAULT_IMG, config.VAULT_MODE, config.OLLAMA_URL) = cls._saved
        os.environ.pop("MEMORYVAULT_PIN_FILE", None)

    def setUp(self):
        auth.SESSIONS.clear()
        auth.LOCKOUT.clear()
        auth.set_pin(PIN)
        debuglog.clear()

    def req(self, path, headers=None, method="GET", body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
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
        return {**TLS, "Cookie": "; ".join(f"{k}={v}" for k, v in jar.items())}

    def state(self, since=None):
        path = "/api/debug/state" + (f"?since={since}" if since else "")
        status, headers, body, _ = self.req(path, self.signed_in())
        self.assertEqual(status, 200, body)
        return json.loads(body), body, headers


class GateTest(_Base):
    def test_route_is_pin_gated_in_the_table(self):
        self.assertEqual(auth.classify("/api/debug/state"), auth.PIN)

    def test_rejects_unauthenticated(self):
        debuglog.push("log", "canary line that must not leak")
        status, _, body, _ = self.req("/api/debug/state", TLS)
        self.assertEqual(status, 401)
        self.assertNotIn(b"canary", body)

    def test_rejects_bogus_session_cookie(self):
        status, *_ = self.req("/api/debug/state",
                              {**TLS, "Cookie": f"{auth.SESSION_COOKIE}=forged"})
        self.assertEqual(status, 401)

    def test_plain_http_refused(self):
        status, *_ = self.req("/api/debug/state", {"Accept": "application/json"})
        self.assertEqual(status, 403)


class SchemaTest(_Base):
    def test_schema(self):
        debuglog.push("warn", "hello from the test")
        d, _, headers = self.state()
        self.assertEqual(d["product"], "constellation")
        self.assertRegex(d["now"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z$")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertTrue(any(e["msg"] == "hello from the test" and e["level"] == "warn"
                            for e in d["log"]))
        for e in d["log"]:
            self.assertEqual(set(e), {"t", "level", "msg"})
            self.assertIn(e["level"], ("log", "warn", "error"))
            self.assertLessEqual(len(e["msg"]), 2000)
        labels = [c["label"] for c in d["checks"]]
        self.assertTrue(3 <= len(labels) <= 8, labels)
        for want in ("Library DB", "Pipeline", "Vision model", "Disk", "Vault"):
            self.assertIn(want, labels)
        for c in d["checks"]:
            self.assertEqual(set(c), {"label", "ok", "detail"})
            self.assertIn(c["ok"], (True, False, None))
            self.assertIsInstance(c["detail"], str)
        by = {c["label"]: c for c in d["checks"]}
        self.assertIs(by["Library DB"]["ok"], True)
        self.assertIs(by["Vision model"]["ok"], False)     # nothing on :9
        self.assertIs(by["Vault"]["ok"], False)            # luks, not mounted

    def test_since_tails(self):
        debuglog.push("log", "line A")
        d, *_ = self.state()
        t_a = next(e["t"] for e in d["log"] if e["msg"] == "line A")
        debuglog.push("log", "line B")
        debuglog.push("error", "line C")
        d2, *_ = self.state(since=t_a)
        self.assertEqual([e["msg"] for e in d2["log"]], ["line B", "line C"])
        d3, *_ = self.state(since=d2["log"][-1]["t"])
        self.assertEqual(d3["log"], [])

    def test_ring_is_bounded_and_ansi_stripped(self):
        for i in range(debuglog.MAX_ENTRIES + 50):
            debuglog.push("log", f"\x1b[32mn{i}\x1b[0m")
        e = debuglog.entries()
        self.assertEqual(len(e), debuglog.MAX_ENTRIES)
        self.assertEqual(e[-1]["msg"], f"n{debuglog.MAX_ENTRIES + 49}")
        debuglog.push("log", "x" * 5000)
        self.assertEqual(len(debuglog.entries()[-1]["msg"]), 2000)


class RedactionTest(_Base):
    SECRETS = ["tok3nV4lueZZ", "sk-live-abcdef123", "hunter2smtp", "B3arerOpaque99", "8812"]

    def test_planted_secrets_and_vault_paths_never_reach_the_payload(self):
        vault_file = config.VAULT_MOUNT / "flagged" / "IMG_0042.jpg"
        # via the logging handler (root logger path) ...
        lg = logging.getLogger("mv.test.debug")
        h = debuglog._Handler()
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
        try:
            # assembled at runtime so credential scanners don't flag the fixture
            lg.warning("vision call with %s=%s failed", "api" + "_key", "sk-live-" + "abcdef123")
            lg.error("moved %s into the vault", vault_file)
        finally:
            lg.removeHandler(h)
        # ... and via the stdout/stderr tee (bare print lines)
        out = debuglog._Tee(io.StringIO(), "stdout")
        err = debuglog._Tee(io.StringIO(), "stderr")
        print("auth token=tok3nV4lueZZ refreshed", file=out)
        print("smtp: SMTP_PASS=hunter2smtp login", file=out)
        print("Authorization: Bearer B3arerOpaque99", file=err)
        print('{"pin": "8812"} rejected', file=out)
        print(f"luks open {config.VAULT_IMG} -> /dev/mapper/memoryvault", file=err)
        d, raw, _ = self.state()
        text = raw.decode()
        for s in self.SECRETS:
            self.assertNotIn(s, text, s)
        self.assertNotIn("sealedvault", text)
        self.assertNotIn("IMG_0042", text)
        self.assertNotIn("/dev/mapper/memoryvault", text)
        msgs = [e["msg"] for e in d["log"]]
        self.assertTrue(any("[vault path]" in m for m in msgs), msgs)
        self.assertTrue(any("[redacted]" in m for m in msgs), msgs)
        # the lines themselves survive (only the secret part is scrubbed)
        self.assertTrue(any(m.startswith("auth token=") for m in msgs), msgs)

    def test_library_root_is_shortened(self):
        debuglog.push("log", f"thumb missing: {config.LIBRARY_ROOT}/thumbnails/ab.jpg")
        self.assertEqual(debuglog.entries()[-1]["msg"], "thumb missing: <library>/thumbnails/ab.jpg")

    def test_checks_never_echo_vault_path(self):
        _, raw, _ = self.state()
        self.assertNotIn(str(config.VAULT_MOUNT).encode(), raw)
        self.assertNotIn(str(self.tmp).encode(), raw)


class BoundedTest(_Base):
    def test_hung_vision_server_still_answers_under_3s(self):
        # accepts TCP, never answers: the classic wedged-Ollama shape
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(8)
        saved = config.OLLAMA_URL
        config.OLLAMA_URL = f"http://127.0.0.1:{s.getsockname()[1]}/api/generate"
        try:
            hdr = self.signed_in()
            t0 = time.monotonic()
            status, _, body, _ = self.req("/api/debug/state", hdr)
            took = time.monotonic() - t0
        finally:
            config.OLLAMA_URL = saved
            s.close()
        self.assertEqual(status, 200)
        self.assertLess(took, 3.0)
        vis = next(c for c in json.loads(body)["checks"] if c["label"] == "Vision model")
        self.assertIsNot(vis["ok"], True)

    def test_db_gone_is_reported_not_fatal(self):
        saved = srv.Handler.condb
        srv.Handler.condb = srv.ConstellationDB(self.tmp / "nope" / "missing.db")
        try:
            d, *_ = self.state()
        finally:
            srv.Handler.condb = saved
        by = {c["label"]: c for c in d["checks"]}
        self.assertIs(by["Library DB"]["ok"], False)
        self.assertNotIn(str(self.tmp), json.dumps(d))

    def test_checks_do_not_write(self):
        # dir-mode vault probe must not create folders (vault.is_mounted does)
        saved = config.VAULT_MODE
        config.VAULT_MODE = "dir"
        try:
            self.state()
        finally:
            config.VAULT_MODE = saved
        self.assertFalse(config.VAULT_MOUNT.exists())


class OverlayTest(_Base):
    def test_every_page_carries_the_include(self):
        hdr = self.signed_in()
        hdr["Accept"] = "text/html"
        for path in ("/", "/wall", "/gallery", "/menu", "/progress", "/login", "/curation"):
            status, _, body, _ = self.req(path, hdr)
            self.assertEqual(status, 200, path)
            self.assertEqual(body.count(srv.DEBUG_TAG), 1, path)

    def test_script_is_served_and_safe(self):
        status, headers, body, _ = self.req("/static/debug.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        js = body.decode()
        self.assertIn("/api/debug/state", js)
        self.assertIn("since=", js)
        self.assertIn("e.ctrlKey && e.shiftKey", js)
        self.assertIn("XMLHttpRequest", js)          # not pin.js's redirecting fetch
        self.assertNotIn("fetch(", js)
        self.assertNotIn("innerHTML", js)            # log text is never parsed as HTML
        for b in ("Pause", "Clear", "Refresh", "Close"):
            self.assertIn(f'"{b}"', js)

    def test_menu_has_a_visible_entry_point(self):
        status, _, body, _ = self.req("/menu", {"Accept": "text/html"})
        self.assertIn(b"constellation:toggle-debug", body)
        self.assertIn(b"Debug Console", body)


class TeeTest(unittest.TestCase):
    def test_tee_writes_through_and_never_raises(self):
        orig = io.StringIO()
        tee = debuglog._Tee(orig, "stdout")
        saved = debuglog.push

        def boom(*a, **k):
            raise RuntimeError("ring exploded")
        debuglog.push = boom
        try:
            tee.write("first\nsecond part")
            tee.write(" done\n")
        finally:
            debuglog.push = saved
        self.assertEqual(orig.getvalue(), "first\nsecond part done\n")

    def test_traceback_block_stays_error_level(self):
        debuglog.clear()
        tee = debuglog._Tee(io.StringIO(), "stderr")
        tee.write("Traceback (most recent call last):\n  File \"x.py\", line 1\n"
                  "    boom()\nValueError: nope\nplain stderr afterwards\n")
        lv = [(e["level"], e["msg"]) for e in debuglog.entries()]
        self.assertEqual([x[0] for x in lv], ["error"] * 4 + ["warn"], lv)

    def test_install_is_idempotent_in_a_real_process(self):
        code = (
            "import sys, logging; sys.path.insert(0, %r)\n"
            "from memoryvault.constellation import debuglog as d\n"
            "d.install_tap(); d.install_tap()\n"
            "print('hello stdout'); print('oops Traceback', file=sys.stderr)\n"
            "logging.getLogger('x').warning('from logging')\n"
            "import json; sys.__stdout__.write(json.dumps(d.entries()))\n" % str(SCRIPTS))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hello stdout", r.stdout)       # original stream intact
        self.assertIn("oops Traceback", r.stderr)
        ents = json.loads(r.stdout[r.stdout.index("["):])
        msgs = {(e["level"], e["msg"]) for e in ents}
        self.assertIn(("log", "hello stdout"), msgs)
        self.assertIn(("error", "oops Traceback"), msgs)
        self.assertIn(("warn", "x: from logging"), msgs)
        self.assertEqual(sum("tap installed" in e["msg"] for e in ents), 1)


if __name__ == "__main__":
    unittest.main()
