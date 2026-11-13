"""Static contracts for the in-place wall/menu funnel."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

STATIC = Path(__file__).resolve().parents[1] / "memoryvault" / "constellation" / "static"


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self.in_quick = False
        self.in_anchor = False
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav" and attrs.get("aria-label") == "Quick links":
            self.in_quick = True
        elif self.in_quick and tag == "a":
            self.in_anchor = True
            self.current = {"href": attrs.get("href"), "label": ""}

    def handle_data(self, data):
        if self.in_anchor:
            self.current["label"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.in_anchor:
            self.items.append({"href": self.current["href"], "label": self.current["label"].strip()})
            self.in_anchor = False
        elif tag == "nav" and self.in_quick:
            self.in_quick = False


class FunnelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.menu = (STATIC / "menu.html").read_text()
        cls.wall = (STATIC / "wall.html").read_text()

    def test_quick_links_target_existing_screens_and_local_logs(self):
        parser = Links()
        parser.feed(self.menu)
        self.assertEqual(
            parser.items,
            [
                {"label": "Photos", "href": "/gallery"},
                {"label": "Edit", "href": "/curation"},
                {"label": "Settings", "href": "/start"},
                {"label": "Help", "href": "/start"},
                {"label": "Logs", "href": "#logs"},
            ],
        )
        self.assertIn("constellation:toggle-debug", self.menu)

    def test_first_run_tour_is_skippable_and_locally_persisted(self):
        self.assertIn('role="dialog" aria-modal="true"', self.wall)
        self.assertIn('id="spotlightSkip"', self.wall)
        self.assertIn('id="spotlightClose"', self.wall)
        self.assertIn('id="spotlightEscape" hidden', self.wall)
        self.assertIn('document.getElementById("spotlightEscape").hidden = tour.length <= 1', self.wall)
        self.assertIn('id="spotlightNext"', self.wall)
        self.assertIn('localStorage.setItem("constellation.wall.spotlight.dismissed", "1")', self.wall)
        self.assertIn('localStorage.getItem("constellation.wall.spotlight.dismissed") !== "1"', self.wall)
        self.assertIn('if (ev.key === "Escape" && spotlight.classList.contains("on")) finishTour()', self.wall)

    def test_empty_ingest_links_are_present_and_open_sky_is_same_origin(self):
        self.assertIn('<a class="primary" href="/start">Add photos</a>', self.wall)
        self.assertIn('<a href="/" id="openSky">Open the sky</a>', self.wall)
        self.assertIn('<a href="/menu">Menu</a>', self.wall)

    def test_no_new_routes_or_remote_telemetry_added(self):
        self.assertNotIn("https://", self.menu + self.wall)
        self.assertNotIn("navigator.sendBeacon", self.menu + self.wall)
        self.assertNotIn("fetch(", self.menu)
        self.assertNotIn("XMLHttpRequest", self.menu)


if __name__ == "__main__":
    unittest.main()
