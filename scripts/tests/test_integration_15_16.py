"""PR #15 + PR #16 composed: the union must keep both sides' guarantees.

PR #15 added the Getting started page (/start), the PIN-gated observational
/api/setup/where and per-page "Menu" chips. PR #16 moved every page shell into
the early `PAGES` table (served before the DB opens, as raw UTF-8 bytes) and
injects one global way-home control (static/nav.js). A careless merge loses
/start from the early table (so it 503s on a missing library, or 404s), injects
nav twice, or gives a page two Menu buttons. This pins the union.

Synthetic temp library only; nav.js is replayed under Node with a tiny DOM stub.
"""

import json
import pathlib
import shutil
import subprocess
from pathlib import Path
from unittest import mock

import pytest

import test_nav_routes as nav  # same-dir helper; only the base class is reused
from memoryvault import config
from memoryvault.constellation import auth
from memoryvault.constellation import server as srv

STATIC = Path(srv.__file__).resolve().parent / "static"
NODE = shutil.which("node")


class UnionPagesTest(nav._Server):
    def test_start_is_an_early_open_page_shell(self):
        self.assertEqual(srv.PAGES.get("/start"), "start.html")
        self.assertEqual(auth.classify("/start"), auth.OPEN)

    def test_every_page_carries_exactly_one_nav_tag(self):
        signed = self.signed_in()
        for p in srv.PAGES:
            hdrs = signed if auth.classify(p) == auth.PIN else nav.HTML
            status, _, body, _ = self.req(p, hdrs)
            self.assertEqual(status, 200, p)
            self.assertEqual(body.count(srv.NAV_TAG), 1, p)

    def test_start_survives_windows_ansi_decoding(self):
        with mock.patch.object(pathlib.Path, "read_text", nav._windows_read_text):
            status, _, body, _ = self.req("/start", nav.HTML)
        self.assertEqual(status, 200)
        self.assertEqual(body.count(srv.NAV_TAG), 1)
        self.assertIn('href="/menu"'.encode(), body)


class UnionMissingLibraryTest(nav._Server):
    db_present = False

    def setUp(self):
        super().setUp()
        self._saved_vault = (config.VAULT_MODE, config.VAULT_MOUNT)
        config.VAULT_MODE = "dir"
        config.VAULT_MOUNT = self.tmp / "vault-not-created"

    def tearDown(self):
        config.VAULT_MODE, config.VAULT_MOUNT = self._saved_vault

    def test_start_page_needs_no_db(self):
        status, _, body, _ = self.req("/start", nav.HTML)
        self.assertEqual(status, 200)
        self.assertIn(srv.NAV_TAG, body)
        self.assertFalse(config.DB_PATH.exists(), "serving /start created the DB")

    def test_setup_where_answers_before_db_and_creates_nothing(self):
        status, _, body, _ = self.req("/api/setup/where", self.signed_in())
        self.assertEqual(status, 200)
        w = json.loads(body)
        self.assertEqual(w["vault_state"], "dir-not-created")
        self.assertFalse(config.VAULT_MOUNT.exists(), "GET created the vault dir")
        self.assertFalse(config.DB_PATH.exists(), "GET created the DB")

    def test_setup_where_still_needs_the_pin(self):
        status, _, _, _ = self.req("/api/setup/where", nav.TLS)
        self.assertEqual(status, 401)


NAV_HARNESS = r"""
const PAGE = %PAGE%;
const PATH = %PATH%;
const appended = [];
function node(tag) {
  return { tag, children: [], firstChild: null, attrs: {}, id: "", textContent: "",
    classList: { add() {}, remove() {} },
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener() {},
    appendChild(c) { this.children.push(c); this.firstChild = this.children[0]; return c; } };
}
global.window = { addEventListener() {} };
global.location = { pathname: PATH, origin: "https://constellation.test" };
global.history = { back() {} };
global.document = {
  referrer: "", readyState: "complete",
  head: node("head"), body: { appendChild(c) { appended.push(c); return c; } },
  createElement: node,
  getElementById() { return null; },
  // the only selector nav.js asks: does the page already show its own Menu link?
  querySelector(sel) { return PAGE.includes('href="/menu"') ? {} : null; },
  addEventListener() {},
};
%NAV%
const bars = appended.filter((n) => n.id === "cst-nav");
const menus = bars.flatMap((b) => b.children).filter((c) => c.id === "cst-nav-menu");
process.stdout.write(JSON.stringify({ bars: bars.length, navMenus: menus.length,
  ownMenus: (PAGE.match(/href="\/menu"/g) || []).length }));
"""


def _nav_replay(path, page_text):
    js = (NAV_HARNESS.replace("%PAGE%", json.dumps(page_text))
          .replace("%PATH%", json.dumps(path))
          .replace("%NAV%", (STATIC / "nav.js").read_text(encoding="utf-8")))
    done = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestOneMenuButtonPerPage:
    @pytest.mark.parametrize("path", sorted(srv.PAGES))
    def test_page_never_gets_a_second_menu_button(self, path):
        page = (STATIC / srv.PAGES[path]).read_text(encoding="utf-8")
        r = _nav_replay(path, page)
        if path in ("/menu", "/home"):
            assert r["navMenus"] == 0 and r["bars"] == 0
        elif r["ownMenus"]:
            assert r["navMenus"] == 0, f"{path}: nav.js added a Menu next to the page's own"
        else:
            assert r["navMenus"] == 1, f"{path}: no way back to the menu"

    def test_page_without_its_own_menu_link_gets_exactly_one(self):
        r = _nav_replay("/somewhere", "<html><body><p>no links</p></body></html>")
        assert r == {"bars": 1, "navMenus": 1, "ownMenus": 0}

    def test_start_and_wall_keep_their_own_menu(self):
        for path in ("/start", "/wall"):
            page = (STATIC / srv.PAGES[path]).read_text(encoding="utf-8")
            r = _nav_replay(path, page)
            assert r["ownMenus"] >= 1 and r["navMenus"] == 0, path
