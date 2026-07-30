"""Tests for the 2026-07-30 lock-storm fixes: the Constellation server's graceful
"library busy" 503 and the placards single-instance guard. Mutation review
of PR #21 showed neither was covered — a neutered guard and an inverted 503
branch both passed the suite. These make those mutations fail."""

import pathlib
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3

from memoryvault import config, db
from memoryvault import placards as placards_mod
from memoryvault.constellation.server import ConstellationDB, Handler


class GuardMatchTest(unittest.TestCase):
    def test_real_invocations_match(self):
        for argv in (
            ["/opt/memoryvault/venv/bin/python",
             str(pathlib.Path(__file__).resolve().parents[1] / "mvault"),
             "placards"],
            ["python3", "./mvault", "placards", "--shard", "0/2"],
            ["sudo", "mvault", "placards"],
            ["timeout", "3600", "mvault", "placards", "--limit", "5"],
        ):
            self.assertTrue(placards_mod._is_placards_cmd(argv), argv)

    def test_mentions_do_not_match(self):
        for argv in (
            # a shell whose -c script merely mentions the phrase (the
            # review agent's own bash heredoc false-positived on this)
            ["bash", "-c", "echo mvault placards is running"],
            ["vim", "placards.py"],
            ["mvault", "tag", "--retag"],
            [],
        ):
            self.assertFalse(placards_mod._is_placards_cmd(argv), argv)


class GuardBehaviorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mv-guard-test-"))
        config.LIBRARY_ROOT = self.tmp / "library"
        config.DB_PATH = config.LIBRARY_ROOT / "photos.db"
        self.conn = db.init(config.DB_PATH)
        self._orig = placards_mod._other_sweeps

    def tearDown(self):
        placards_mod._other_sweeps = self._orig
        self.conn.close()

    def test_second_sweep_skips(self):
        placards_mod._other_sweeps = lambda: ["pid 999: mvault placards"]
        out = placards_mod.placards(self.conn)
        self.assertIn("skipped", out)
        self.assertEqual(out["running"], ["pid 999: mvault placards"])

    def test_other_sweeps_sees_live_decoy(self):
        # exercise the real /proc scan end-to-end: a live process whose argv
        # is (.../mvault, placards) must be found. Catches the mutation where
        # the scanner stops consulting the matcher.
        import subprocess
        import time
        script = self.tmp / "mvault"
        script.write_text("#!/bin/sh\nsleep 30\n")
        script.chmod(0o755)
        p = subprocess.Popen([str(script), "placards"])
        try:
            time.sleep(0.3)  # let it through exec so /proc shows real argv
            found = placards_mod._other_sweeps()
            self.assertTrue(any(f"pid {p.pid}:" in s for s in found), found)
        finally:
            p.terminate()
            p.wait()

    def test_force_runs_anyway(self):
        placards_mod._other_sweeps = lambda: ["pid 999: mvault placards"]
        out = placards_mod.placards(self.conn, force=True)
        # an empty library sweeps zero photos, but it must get past the guard
        self.assertNotIn("skipped", out)


class Busy503Test(unittest.TestCase):
    """Drive the real handler over HTTP: a locked/busy OperationalError
    from an endpoint body must come back as JSON 503 'library busy'; any
    other OperationalError stays a 500."""

    ERROR = "database is locked"

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="mv-busy-test-"))
        db.init(cls.tmp / "photos.db").close()

        class StubDB(ConstellationDB):
            def categories(self, conn, **kw):
                raise sqlite3.OperationalError(Busy503Test.ERROR)

        cls._saved_db = getattr(Handler, "condb", None)
        Handler.condb = StubDB(cls.tmp / "photos.db")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        Handler.condb = cls._saved_db

    def _get_categories(self):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/api/categories",
                    timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_locked_is_graceful_503(self):
        Busy503Test.ERROR = "database is locked"
        status, body = self._get_categories()
        self.assertEqual(status, 503)
        self.assertEqual(body, {"error": "library busy"})

    def test_busy_is_graceful_503(self):
        Busy503Test.ERROR = "database is busy"
        status, body = self._get_categories()
        self.assertEqual(status, 503)
        self.assertEqual(body, {"error": "library busy"})

    def test_other_operational_error_stays_500(self):
        Busy503Test.ERROR = "no such table: tags"
        status, body = self._get_categories()
        self.assertEqual(status, 500)
        self.assertEqual(body, {"error": "internal"})


if __name__ == "__main__":
    unittest.main()
