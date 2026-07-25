"""Core pipeline tests — runnable with plain `python3 tests/test_core.py`.

Covers the SPEC.md release-gate behaviors that can be verified without a
GPU, Ollama, or a LUKS device: ingest/dedup correctness, screening verdict
logic incl. fail-safe error semantics, vault scrubbing, and the Brain's
neighborhood query.
"""

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw

from memoryvault import config, db


def make_photo(path: Path, seed: int = 0, size=(400, 300)):
    img = Image.new("RGB", size, (40 + seed * 13 % 200, 80, 120))
    d = ImageDraw.Draw(img)
    d.ellipse([50 + seed * 5, 50, 200 + seed * 5, 200], fill=(220, 180, 60))
    d.rectangle([250, 100, 380, 280], fill=(60, 160, 220))
    img.save(path, "JPEG", quality=90)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mv-test-"))
        self.src = self.tmp / "source"
        self.src.mkdir()
        config.LIBRARY_ROOT = self.tmp / "library"
        config.DB_PATH = config.LIBRARY_ROOT / "photos.db"
        config.VAULT_MOUNT = self.tmp / "vault-mount"
        self.conn = db.init(config.DB_PATH)

    def _discover_ingest(self):
        from memoryvault.discover import discover
        from memoryvault.ingest import ingest

        discover(self.conn, self.src)
        return ingest(self.conn)

    def test_ingest_and_exact_dup(self):
        make_photo(self.src / "a.jpg", seed=1)
        make_photo(self.src / "b.jpg", seed=2)
        # exact duplicate: same bytes, different name/folder
        (self.src / "sub").mkdir()
        (self.src / "sub" / "a_copy.jpg").write_bytes(
            (self.src / "a.jpg").read_bytes()
        )
        stats = self._discover_ingest()
        self.assertEqual(stats["canonical"], 2)
        self.assertEqual(stats["duplicate"], 1)
        # identity is sha256: same-stem files from different dirs don't collide
        photos = self.conn.execute("SELECT * FROM photos").fetchall()
        self.assertEqual(len(photos), 2)
        for p in photos:
            self.assertTrue((config.LIBRARY_ROOT / p["library_path"]).exists())
            thumb = config.LIBRARY_ROOT / "thumbnails" / f"{p['sha256'][:16]}.jpg"
            self.assertTrue(thumb.exists())
        # source untouched
        self.assertTrue((self.src / "a.jpg").exists())

    def test_near_dup_clusters_resized_copy(self):
        from memoryvault.dedup import dedup

        make_photo(self.src / "orig.jpg", seed=3, size=(800, 600))
        with Image.open(self.src / "orig.jpg") as img:
            img.resize((400, 300)).save(self.src / "small.jpg", "JPEG", quality=70)
        make_photo(self.src / "other.jpg", seed=40)
        self._discover_ingest()
        stats = dedup(self.conn, quarantine=True)
        self.assertEqual(stats["near_groups"], 1)
        self.assertEqual(stats["quarantined"], 1)
        # keeper is the higher-resolution one
        g = self.conn.execute("SELECT * FROM duplicate_groups").fetchone()
        keeper = self.conn.execute(
            "SELECT width FROM photos WHERE id = ?", (g["keeper_photo_id"],)
        ).fetchone()
        self.assertEqual(keeper["width"], 800)
        # loser moved to duplicates/, decision pending — nothing deleted
        pending = self.conn.execute(
            "SELECT COUNT(*) c FROM duplicate_members WHERE decision='pending'"
        ).fetchone()["c"]
        self.assertEqual(pending, 1)

    def test_screening_verdicts_and_failsafe(self):
        from memoryvault.screen import screen_verdict, ScreenError, SAFE, VAULT, REVIEW, ERROR

        def confirm_yes(p):
            return True

        def confirm_no(p):
            return False

        def confirm_boom(p):
            raise ScreenError("ollama down")

        def score(v):
            return lambda p: v

        def score_boom(p):
            raise ScreenError("classifier missing")

        # low score → safe without pass 2 (t_low default is 0.05 now)
        self.assertEqual(screen_verdict("x", score(0.01), confirm_boom)[0], SAFE)
        # flagged + confirmed → vault
        self.assertEqual(screen_verdict("x", score(0.6), confirm_yes)[0], VAULT)
        # flagged, VLM disagrees, mid score → safe
        self.assertEqual(screen_verdict("x", score(0.5), confirm_no)[0], SAFE)
        # hard disagreement → human review
        self.assertEqual(screen_verdict("x", score(0.95), confirm_no)[0], REVIEW)
        # THE fail-safe: any error is ERROR, never a quarantine verdict
        self.assertEqual(screen_verdict("x", score_boom, confirm_yes)[0], ERROR)
        self.assertEqual(screen_verdict("x", score(0.6), confirm_boom)[0], ERROR)

    def test_screen_halts_without_vault_and_scrubs_with_it(self):
        from memoryvault import vault
        from memoryvault.screen import screen

        make_photo(self.src / "p1.jpg", seed=5)
        make_photo(self.src / "p2.jpg", seed=60)
        self._discover_ingest()

        # vault not mounted → the batch must refuse to start
        with self.assertRaises(vault.VaultUnavailable):
            screen(self.conn, score_fn=lambda p: 0.0, confirm_fn=lambda p: False)

        # simulate a mounted vault (plain dir + patched check for the test)
        config.VAULT_MOUNT.mkdir(parents=True)
        orig = vault.is_mounted
        vault.is_mounted = lambda: True
        try:
            import memoryvault.screen as screen_mod

            stats = screen(
                self.conn,
                score_fn=lambda p: 0.99,
                confirm_fn=lambda p: True,
            )
        finally:
            vault.is_mounted = orig
        self.assertEqual(stats["vault"], 2)
        # invariant #3: zero trace in the DB, files inside the vault, thumbs gone
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM photos").fetchone()["c"], 0
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM files").fetchone()["c"], 0
        )
        self.assertEqual(len(list(config.VAULT_MOUNT.iterdir())), 2)
        thumbs = list((config.LIBRARY_ROOT / "thumbnails").glob("*.jpg"))
        self.assertEqual(thumbs, [])
        funnel = db.funnel(self.conn)
        self.assertEqual(funnel["vaulted_total"], 2)

    def test_tag_stores_rows_and_exif_year_wins(self):
        from memoryvault.tag import tag

        make_photo(self.src / "t.jpg", seed=7)
        self._discover_ingest()
        self.conn.execute(
            "UPDATE photos SET status='screened', taken_at='2019-06-01T12:00:00'"
        )
        fake = {"people": ["Alex"], "occasion": "Birthday", "year": "2024"}
        stats = tag(self.conn, vision_fn=lambda p, prompt, fmt=None: fake)
        self.assertEqual(stats["tagged"], 1)
        year = self.conn.execute(
            "SELECT value FROM tags WHERE dimension='year'"
        ).fetchone()["value"]
        self.assertEqual(year, "2019")  # EXIF beats model guess

    def test_edges_and_neighborhood(self):
        from memoryvault.tag import tag
        from memoryvault.edges import compute_edges
        from memoryvault.brain.server import BrainDB

        for i in range(4):
            make_photo(self.src / f"e{i}.jpg", seed=100 + i * 17)
        self._discover_ingest()
        self.conn.execute("UPDATE photos SET status='screened'")
        people = [["Alex"], ["Alex"], ["Bailey"], ["Alex", "Bailey"]]
        rows = self.conn.execute("SELECT id FROM photos ORDER BY id").fetchall()
        for row, ppl in zip(rows, people):
            self.conn.execute(
                "UPDATE photos SET taken_at=? WHERE id=?",
                (datetime(2020, 5, 17, 10).isoformat(), row["id"]),
            )
            from memoryvault.tag import store_tags, load_schema

            store_tags(self.conn, row["id"], {"location": "Beach"},
                       load_schema(), None)
            # people tags come from face recognition (schema v2), not vision
            for name in ppl:
                self.conn.execute(
                    "INSERT OR REPLACE INTO tags(photo_id, dimension, value, "
                    "model_version) VALUES (?, 'people', ?, 'faces-1.0')",
                    (row["id"], name),
                )
            self.conn.execute(
                "UPDATE photos SET status='tagged' WHERE id=?", (row["id"],)
            )
        self.conn.commit()
        stats = compute_edges(self.conn)
        self.assertGreater(stats["edges"], 0)

        bdb = BrainDB(config.DB_PATH)
        n = bdb.neighborhood(self.conn, rows[0]["id"], k=8)
        self.assertEqual(n["focus"], rows[0]["id"])
        self.assertGreaterEqual(len(n["nodes"]), 2)
        relations = {e["relation"] for e in n["edges"]}
        self.assertIn("same-person", relations)

    def test_notes_generation_preserves_manual_block(self):
        from memoryvault.notes import generate

        config.MEMORYVAULT_ROOT = self.tmp / "obsidian"
        make_photo(self.src / "n.jpg", seed=9)
        self._discover_ingest()
        row = self.conn.execute("SELECT id FROM photos").fetchone()
        self.conn.execute(
            "UPDATE photos SET status='tagged', taken_at='2021-03-04T09:00:00'"
        )
        self.conn.execute(
            "INSERT INTO tags(photo_id, dimension, value, model_version) "
            "VALUES (?, 'people', 'Alex', 'test')",
            (row["id"],),
        )
        stats = generate(self.conn)
        self.assertEqual(stats["notes"], 1)
        note = next((config.MEMORYVAULT_ROOT / "Photos" / "2021").glob("*.md"))
        content = note.read_text()
        self.assertIn("[[People/Alex|Alex]]", content)
        # add a manual annotation, regenerate, verify it survives
        note.write_text(
            content.replace(
                "<!-- manual -->\n<!-- /manual -->",
                "<!-- manual -->\nthe day we got the dog\n<!-- /manual -->",
            )
        )
        generate(self.conn)
        self.assertIn("the day we got the dog", note.read_text())
        index = (config.MEMORYVAULT_ROOT / "People" / "Alex.md").read_text()
        self.assertIn("2021", index)


    def test_calibrate_sweep_and_recommendation(self):
        from memoryvault.calibrate import sweep, recommend

        safe = [0.01, 0.05, 0.1, 0.3, 0.02]      # one awkward safe photo at 0.3
        flagged = [0.6, 0.9, 0.45, 0.99]
        rows = sweep(safe, flagged)
        rec = recommend(rows, target_recall=0.99)
        self.assertIsNotNone(rec)
        # highest threshold still catching all flagged (min flagged = 0.45)
        self.assertLessEqual(rec["threshold"], 0.45)
        self.assertEqual(rec["recall"], 1.0)
        # impossible target when a flagged item scores below every threshold
        rows2 = sweep(safe, [0.01])
        self.assertIsNone(recommend(rows2, target_recall=0.99))

    def test_migrate_quarantine_moves_and_removes(self):
        from memoryvault import vault
        from memoryvault.migrate import migrate_quarantine

        qdir = self.tmp / "Quarantine"
        qdir.mkdir()
        make_photo(qdir / "private1.jpg", seed=21)
        make_photo(qdir / "private2.jpg", seed=22)
        config.VAULT_MOUNT.mkdir(parents=True)
        orig = vault.is_mounted
        vault.is_mounted = lambda: True
        try:
            stats = migrate_quarantine(qdir)
        finally:
            vault.is_mounted = orig
        self.assertEqual(stats["moved"], 2)
        self.assertFalse(qdir.exists())  # emptied and removed
        self.assertEqual(len(list(config.VAULT_MOUNT.glob("*.jpg"))), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
