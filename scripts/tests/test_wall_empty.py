"""The empty wall's copy must count what can actually be SHOWN, not what was checked.

`/api/progress` reports `screen_done` (every photo the safety check finished,
including the ones it sent to the Personal Vault or to adult review) and
`viewable` (screened, in the library, not Trash). Only `viewable` can ever
reach a wall. A finished sweep where everything went to the vault must not
say "ready 8" and must not tell the family to wait for pictures that will
never come.

This replays the real `updateEmpty()` from wall.html under Node with tiny
DOM/fetch stubs — no browser, no server, no photos.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WALL = (Path(__file__).resolve().parents[1]
        / "memoryvault" / "constellation" / "static" / "wall.html")
NODE = shutil.which("node")

HARNESS = r"""
const els = {};
const el = (id) => (els[id] = els[id] || { textContent: "",
  classList: { _on: false, toggle(_c, v) { this._on = !!v; }, contains() { return this._on; } } });
const document = { getElementById: el };
const emptyEl = el("empty");
const pool = [];
let P = null;
const fetch = async () => ({ json: async () => P });
%SRC%
(async () => {
  const out = [];
  for (const p of %CASES%) {
    P = p; els.emptyMsg = null; els.emptyLive = null;
    await updateEmpty();
    out.push({ msg: el("emptyMsg").textContent, live: el("emptyLive").textContent });
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


def _source():
    html = WALL.read_text(encoding="utf-8")
    start = html.index("async function updateEmpty()")
    end = html.index("(async function boot()")
    return html[start:end]


def _replay(cases):
    js = HARNESS.replace("%SRC%", _source()).replace("%CASES%", json.dumps(cases))
    done = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _p(**kw):
    base = dict(discovered=0, ingested=0, screen_done=0, vaulted=0, review=0,
                viewable=0, active_stage=None)
    base.update(kw)
    return base


# Checking a photo never guarantees it reaches the wall (vault, review, Trash,
# Removed/Delete tags and filtered walls all keep photos off it), so no branch
# may promise that checked or newly added photos will appear, or when.
UNCONDITIONAL_PROMISES = ("appear here as soon as", "they'll appear", "will appear",
                          "will join", "within a minute", "once they're checked")


def _no_promises(r):
    low = r["msg"].lower()
    for phrase in UNCONDITIONAL_PROMISES:
        assert phrase not in low, f"unconditional promise {phrase!r} in {r['msg']!r}"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
class TestEmptyWallCounts:
    def test_all_vaulted_finished_sweep_is_not_ready_and_promises_nothing(self):
        (r,) = _replay([_p(discovered=8, ingested=8, screen_done=8, vaulted=8)])
        assert "ready 8" not in r["live"] and "ready to view 8" not in r["live"]
        assert "checked 8" in r["live"]
        assert "ready to view 0" in r["live"]
        _no_promises(r)
        assert "finished checking" in r["msg"]
        assert "Personal Vault" in r["msg"]
        assert "ready to view" in r["msg"]

    def test_review_and_vault_with_zero_viewable_is_not_ready(self):
        (r,) = _replay([_p(discovered=5, ingested=5, screen_done=5, vaulted=2, review=3)])
        assert "ready to view 0" in r["live"]
        assert "checked 5" in r["live"]
        _no_promises(r)
        assert "finished checking" in r["msg"]
        assert "were sent for adult review" in r["msg"]
        assert "are waiting" not in r["msg"]

    def test_viewable_photos_are_counted_as_ready(self):
        (r,) = _replay([_p(discovered=8, ingested=8, screen_done=8, vaulted=2, viewable=6)])
        assert "ready to view 6" in r["live"]
        assert "checked 8" in r["live"]
        assert "finished checking" not in r["msg"]
        _no_promises(r)
        assert "6 photos are ready to view" in r["msg"]

    def test_still_sweeping_qualifies_the_wait_message_and_uses_viewable(self):
        (r,) = _replay([_p(discovered=1840, ingested=240, screen_done=240, vaulted=240,
                           active_stage="screen")])
        _no_promises(r)
        assert "reading your newest photos" in r["msg"]
        assert "ready to view" in r["msg"]
        assert "ready to view 0" in r["live"]
        assert "checked 240" in r["live"]

    def test_old_server_without_viewable_falls_back_to_checked_minus_held(self):
        p = _p(discovered=8, ingested=8, screen_done=8, vaulted=8)
        del p["viewable"]
        (r,) = _replay([p])
        assert "ready to view 0" in r["live"]
        _no_promises(r)

    def test_old_server_fallback_also_subtracts_trash(self):
        p = _p(discovered=8, ingested=8, screen_done=8, trash=8)
        del p["viewable"]
        (r,) = _replay([p])
        assert "ready to view 0" in r["live"]
        assert "finished checking" in r["msg"]
        _no_promises(r)

    def test_unconfigured_and_unreachable_copy_is_unchanged(self):
        fresh, = _replay([_p()])
        assert "hasn't been pointed at any photos" in fresh["msg"]
        assert fresh["live"] == ""
        down, = _replay([None])
        assert down["msg"] == "No pictures have reached the wall yet."
