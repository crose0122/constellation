"""Recent-first first sweep (V2 CP2, spec B6).

The installer ingests the newest ~2,000 photos first so the sky has this
year's photos in minutes; the archive backfills overnight. Pinned: newest
mtime first; the cap takes the NEWEST N, not the first N discovered; mtime
(stored as text) sorts numerically, not lexically; unknown mtimes go last;
the progress callback reports a count-up; the CLI emits JSON progress lines.

Run: python3 -m pytest tests/test_recent_first.py -q
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from memoryvault import config, db  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent.parent


def _photo(path: Path, seed: int, mtime: float):
    Image.new("RGB", (320, 240), (seed * 37 % 256, 90, 140)).save(path, "JPEG")
    os.utime(path, (mtime, mtime))


class RecentFirstTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mv-recent-"))
        self.src = self.tmp / "src"
        self.src.mkdir()
        self._saved = (config.LIBRARY_ROOT, config.DB_PATH)
        config.LIBRARY_ROOT = self.tmp / "library"
        config.DB_PATH = config.LIBRARY_ROOT / "photos.db"
        self.conn = db.init(config.DB_PATH)
        # 9 < 10 lexically but not numerically: a text sort would get this wrong
        self.times = {"old.jpg": 9.0e8, "mid.jpg": 1.5e9, "new.jpg": 1.7e9, "newest.jpg": 1.72e9,
                      "ancient.jpg": 99.0}
        for i, (name, t) in enumerate(self.times.items()):
            _photo(self.src / name, i, t)

    def tearDown(self):
        config.LIBRARY_ROOT, config.DB_PATH = self._saved

    def _discover(self):
        from memoryvault.discover import discover
        discover(self.conn, self.src)
        self.conn.commit()

    def _ingested_names(self):
        rows = self.conn.execute(
            "SELECT f.source_path FROM files f WHERE f.disposition != 'discovered'").fetchall()
        return {Path(r[0]).name for r in rows}

    def test_cap_takes_the_newest_not_the_first_discovered(self):
        from memoryvault.ingest import ingest
        self._discover()
        ingest(self.conn, limit=2, recent_first=True)
        self.assertEqual(self._ingested_names(), {"newest.jpg", "new.jpg"})

    def test_mtime_sorts_numerically_not_as_text(self):
        from memoryvault.ingest import ingest
        self._discover()
        ingest(self.conn, limit=4, recent_first=True)
        self.assertNotIn("ancient.jpg", self._ingested_names(),
                         "'99.0' > '1.72e9' as text; must sort as a number")

    def test_unknown_mtime_goes_last(self):
        from memoryvault.ingest import ingest
        self._discover()
        self.conn.execute("UPDATE files SET mtime = NULL WHERE source_path LIKE '%newest.jpg'")
        ingest(self.conn, limit=1, recent_first=True)
        self.assertEqual(self._ingested_names(), {"new.jpg"})

    def test_backfill_gets_the_rest(self):
        from memoryvault.ingest import ingest
        self._discover()
        ingest(self.conn, limit=2, recent_first=True)
        ingest(self.conn)  # overnight backfill, no cap
        self.assertEqual(self._ingested_names(), set(self.times))

    def test_progress_counts_up(self):
        from memoryvault.ingest import ingest
        for i in range(60):
            _photo(self.src / f"bulk{i:03d}.jpg", i + 10, 1.6e9 + i)
        self._discover()
        seen = []
        ingest(self.conn, recent_first=True, progress=lambda d, t: seen.append((d, t)))
        self.assertEqual([d for d, _ in seen], [25, 50])
        self.assertTrue(all(t == 65 for _, t in seen))

    def test_cli_emits_json_progress(self):
        for i in range(30):
            _photo(self.src / f"c{i:02d}.jpg", i + 5, 1.6e9 + i)
        env = {**os.environ, "MEMORYVAULT_LIBRARY_ROOT": str(config.LIBRARY_ROOT)}
        run = lambda *a: subprocess.run([sys.executable, str(SCRIPTS / "mvault"), *a],
                                        env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(run("discover", str(self.src), "--kind", "local").returncode, 0)
        r = run("ingest", "--recent-first", "--progress-json", "--limit", "30")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [json.loads(l) for l in r.stdout.splitlines() if l.startswith('{"progress"')]
        self.assertEqual(lines[0], {"progress": {"done": 25, "total": 30}})


if __name__ == "__main__":
    unittest.main()
