"""Formats + bursts at ingest (V2 CP3, spec B4/B5).

HEIC: original kept byte-for-byte, JPEG rendition is the working file, EXIF
(date, camera, GPS) carried, orientation baked in, rendition viewable by the
server. RAW: archived, never in any stream. >4 GB: skipped before hashing with
a plain reason. Bursts: sharpest kept, rest parked (not deleted), releasable,
idempotent; photos without a time or far apart in time never collapse.

Run: python3 -m pytest tests/test_formats_bursts.py -q
"""

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from memoryvault import config, db  # noqa: E402

try:
    import pillow_heif  # noqa: F401
    HAVE_HEIF = True
except ImportError:
    HAVE_HEIF = False


def _scene(seed=0, size=(800, 600)):
    rng = np.random.default_rng(seed)
    im = Image.new("RGB", size, (90, 140, 200))
    d = ImageDraw.Draw(im)
    for _ in range(40):
        x, y = rng.integers(0, size[0]), rng.integers(0, size[1])
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        d.rectangle([x, y, x + rng.integers(10, 120), y + rng.integers(10, 120)], fill=c)
    return im


def _exif(dt="2024:07:04 18:30:00", model="iPhone 15", orientation=None):
    ex = Image.Exif()
    ex[306] = dt
    ex[272] = model
    ifd = ex.get_ifd(0x8769)
    ifd[36867] = dt
    gps = ex.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (40.0, 26.0, 46.0), "W", (79.0, 58.0, 56.0)
    if orientation:
        ex[0x0112] = orientation
    return ex


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class _Lib(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mv-cp3-"))
        self.src = self.tmp / "Pictures"
        self.src.mkdir()
        self._saved = (config.LIBRARY_ROOT, config.DB_PATH, config.MAX_FILE_BYTES)
        config.LIBRARY_ROOT = self.tmp / "library"
        config.DB_PATH = config.LIBRARY_ROOT / "photos.db"
        self.conn = db.init(config.DB_PATH)

    def tearDown(self):
        config.LIBRARY_ROOT, config.DB_PATH, config.MAX_FILE_BYTES = self._saved

    def ingest(self):
        from memoryvault.discover import discover
        from memoryvault.ingest import ingest
        discover(self.conn, self.src)
        return ingest(self.conn)

    def photo(self, name):
        return self.conn.execute(
            "SELECT p.* FROM photos p JOIN files f ON f.photo_id = p.id "
            "WHERE f.source_path LIKE ?", ("%" + name,)).fetchone()


@unittest.skipUnless(HAVE_HEIF, "pillow-heif not installed")
class HeicTest(_Lib):
    def test_heic_original_kept_and_jpeg_is_the_working_file(self):
        p = self.src / "IMG_0001.HEIC"
        _scene(1).save(p, exif=_exif().tobytes())
        before = _sha(p)
        stats = self.ingest()
        self.assertEqual(stats["canonical"], 1)
        row = self.photo("IMG_0001.HEIC")
        work = config.LIBRARY_ROOT / row["library_path"]
        orig = config.LIBRARY_ROOT / row["original_path"]
        self.assertEqual(work.suffix, ".jpg")
        with Image.open(work) as im:
            self.assertEqual(im.format, "JPEG")
        self.assertEqual(_sha(orig), before, "original HEIC must be byte-identical")
        self.assertEqual(_sha(p), before, "source never touched")
        self.assertEqual(row["sha256"], before, "identity is the original's hash")

    def test_exif_carried_date_camera_gps(self):
        p = self.src / "IMG_0002.heic"
        _scene(2).save(p, exif=_exif().tobytes())
        self.ingest()
        row = self.photo("IMG_0002.heic")
        self.assertEqual(row["taken_at"], "2024-07-04T18:30:00")
        self.assertEqual(row["camera"], "iPhone 15")
        self.assertAlmostEqual(row["gps_lat"], 40.4461, places=3)
        self.assertAlmostEqual(row["gps_lon"], -79.9822, places=3)
        from memoryvault.formats import jpeg_bytes_exif
        jx = jpeg_bytes_exif(config.LIBRARY_ROOT / row["library_path"])
        self.assertEqual(jx["taken_at"], "2024-07-04T18:30:00", "the JPEG itself keeps the date")

    def test_orientation_baked_into_pixels(self):
        p = self.src / "IMG_0003.heic"
        _scene(3, size=(800, 600)).save(p, exif=_exif(orientation=6).tobytes())  # 90° CW
        self.ingest()
        row = self.photo("IMG_0003.heic")
        with Image.open(config.LIBRARY_ROOT / row["library_path"]) as im:
            self.assertEqual(im.size, (600, 800), "portrait after baking orientation")
            self.assertEqual(im.getexif().get(0x0112, 1), 1, "tag reset so viewers don't rotate twice")
        self.assertEqual((row["width"], row["height"]), (600, 800))

    def test_exif_read_from_the_original_not_the_rendition(self):
        # The original is the authority. Make the rendition disagree (no EXIF
        # at all) and prove the row still has the original's date.
        import memoryvault.formats as fm
        real = fm.heic_to_jpeg

        def lossy(src, dest):
            out = real(src, dest)
            with Image.open(dest) as im:
                im.copy().save(dest, "JPEG")          # rewrite without EXIF
            return out
        fm.heic_to_jpeg = lossy
        try:
            p = self.src / "IMG_0005.heic"
            _scene(5).save(p, exif=_exif(dt="2019:12:25 08:00:00").tobytes())
            self.ingest()
        finally:
            fm.heic_to_jpeg = real
        self.assertEqual(self.photo("IMG_0005.heic")["taken_at"], "2019-12-25T08:00:00")

    def test_orientation_note(self):
        # pillow-heif applies HEIF's own rotation on decode, so a HEIC reaches
        # heic_to_jpeg already upright with orientation 1. The transpose/reset
        # in heic_to_jpeg is belt-and-braces for decoders that don't; mutating
        # it is behaviour-neutral with this library (documented in the plan).
        p = self.src / "IMG_0006.heic"
        _scene(6, size=(800, 600)).save(p, exif=_exif(orientation=6).tobytes())
        self.ingest()
        row = self.photo("IMG_0006.heic")
        with Image.open(config.LIBRARY_ROOT / row["library_path"]) as im:
            self.assertEqual(im.getexif().get(0x0112, 1), 1)

    def test_server_display_rendition_works_for_heic(self):
        p = self.src / "IMG_0004.heic"
        _scene(4).save(p, exif=_exif().tobytes())
        self.ingest()
        row = self.photo("IMG_0004.heic")
        from memoryvault.constellation.server import _display_rendition
        out = _display_rendition(self.conn, row["sha256"][:16])
        self.assertIsNotNone(out)
        with Image.open(out) as im:
            self.assertEqual(im.format, "JPEG")


class RawTest(_Lib):
    def test_raw_archived_never_in_any_stream(self):
        raw = self.src / "DSC_0001.NEF"
        raw.write_bytes(b"II*\x00" + os.urandom(4096))     # TIFF-ish header, not decodable
        before = _sha(raw)
        stats = self.ingest()
        self.assertEqual(stats["archived"], 1)
        row = self.photo("DSC_0001.NEF")
        self.assertEqual(row["status"], "archived")
        self.assertIsNone(row["library_path"], "nothing for a stage or page to open")
        self.assertEqual(_sha(config.LIBRARY_ROOT / row["original_path"]), before)
        visible = self.conn.execute(
            "SELECT COUNT(*) FROM photos WHERE status IN ('staged','screened','tagged','noted')").fetchone()[0]
        self.assertEqual(visible, 0)

    def test_new_raw_extensions_are_discovered(self):
        for ext in (".CR3", ".ARW", ".dng", ".RW2", ".raf"):
            (self.src / f"x{ext}").write_bytes(os.urandom(64))
        stats = self.ingest()
        self.assertEqual(stats["archived"], 5)


class SizeCapTest(_Lib):
    def test_over_cap_is_skipped_before_hashing_with_a_plain_reason(self):
        big = self.src / "long-video.mp4"
        big.write_bytes(os.urandom(2048))
        config.MAX_FILE_BYTES = 1024
        import memoryvault.ingest as ing
        hashed = []
        real = ing.sha256_file
        ing.sha256_file = lambda p: hashed.append(p) or real(p)
        try:
            stats = self.ingest()
        finally:
            ing.sha256_file = real
        self.assertEqual(stats["too_large"], 1)
        self.assertEqual(hashed, [], "a too-large file must not be read to hash it")
        err = self.conn.execute("SELECT error FROM errors WHERE stage='ingest'").fetchone()[0]
        self.assertRegex(err, r"long-video\.mp4 is .* Constellation skips files over")
        disp = self.conn.execute("SELECT disposition FROM files").fetchone()[0]
        self.assertEqual(disp, "too-large")

    def test_default_cap_is_4_gb(self):
        self.assertEqual(self._saved[2], 4 * 1000**3)

    def test_message_units_read_naturally(self):
        from memoryvault.formats import too_large_message
        config.MAX_FILE_BYTES = 4 * 1000**3
        self.assertEqual(too_large_message(Path("trip.mov"), 6_400_000_000),
                         "trip.mov is 6.4 GB. Constellation skips files over 4.0 GB "
                         "(usually a very long video); the original is untouched.")
        config.MAX_FILE_BYTES = 5_000_000
        self.assertIn("is 6.0 MB", too_large_message(Path("x.mov"), 6_000_000))


class BurstTest(_Lib):
    def _burst(self, n=5, sharp_index=2, t0="2024:07:04 18:30:00", prefix="B"):
        base = _scene(7)
        from datetime import datetime, timedelta
        start = datetime.strptime(t0, "%Y:%m:%d %H:%M:%S")
        for i in range(n):
            # a real burst: every frame differs a little (hand shake = a few
            # pixels of shift) and all but one carry some motion blur
            shifted = base.transform(base.size, Image.AFFINE, (1, 0, i * 2, 0, 1, i), fillcolor=(90, 140, 200))
            im = shifted if i == sharp_index else shifted.filter(ImageFilter.GaussianBlur(1.5 + 0.7 * i))
            dt = (start + timedelta(milliseconds=300 * i)).strftime("%Y:%m:%d %H:%M:%S")
            im.save(self.src / f"{prefix}{i}.jpg", quality=92, exif=_exif(dt=dt).tobytes())

    def test_sharpest_kept_rest_parked_not_deleted(self):
        from memoryvault.bursts import cull
        self._burst(n=5, sharp_index=2)
        self.ingest()
        stats = cull(self.conn)
        self.assertEqual(stats, {"bursts": 1, "parked": 4, "kept": 1})
        keeper = self.photo("B2.jpg")
        self.assertNotEqual(keeper["status"], "parked")
        for i in (0, 1, 3, 4):
            r = self.photo(f"B{i}.jpg")
            self.assertEqual(r["status"], "parked")
            self.assertEqual(r["parked_by"], keeper["id"])
            self.assertTrue((config.LIBRARY_ROOT / r["library_path"]).exists(), "file kept")
            self.assertTrue((config.LIBRARY_ROOT / "thumbnails" / f"{r['sha256'][:16]}.jpg").exists())

    def test_parked_photos_are_in_no_stream(self):
        from memoryvault.bursts import cull
        self._burst()
        self.ingest()
        cull(self.conn)
        from memoryvault.screen import screen
        config.VAULT_MODE, saved = "dir", config.VAULT_MODE
        config.VAULT_MOUNT, savedm = self.tmp / "vault", config.VAULT_MOUNT
        try:
            screen(self.conn, score_fn=lambda p: 0.0, confirm_fn=lambda p: False)
        finally:
            config.VAULT_MODE, config.VAULT_MOUNT = saved, savedm
        screened = self.conn.execute("SELECT COUNT(*) FROM photos WHERE status='screened'").fetchone()[0]
        self.assertEqual(screened, 1, "only the keeper goes on to the wall")

    def test_release_brings_one_back(self):
        from memoryvault.bursts import cull, release
        self._burst()
        self.ingest()
        cull(self.conn)
        r = self.photo("B0.jpg")
        self.assertTrue(release(self.conn, r["id"]))
        self.assertEqual(self.photo("B0.jpg")["status"], "staged")
        self.assertFalse(release(self.conn, r["id"]), "not parked any more")
        # a released frame is back in the stream; culling again must respect
        # the human choice for frames that are already screened past 'staged'?
        # No: released goes to 'staged' and a re-cull WOULD re-park it, which
        # would silently undo the family's decision. Pin that it does not.
        self.assertEqual(cull(self.conn)["parked"], 0)
        self.assertEqual(self.photo("B0.jpg")["status"], "staged")

    def test_idempotent(self):
        from memoryvault.bursts import cull
        self._burst()
        self.ingest()
        first = cull(self.conn)
        again = cull(self.conn)
        self.assertEqual(first["parked"], 4)
        self.assertEqual(again["parked"], 0)

    def test_same_view_different_days_is_not_a_burst(self):
        from memoryvault.bursts import cull
        base = _scene(9)
        base.save(self.src / "day1.jpg", exif=_exif(dt="2024:07:04 18:30:00").tobytes())
        base.save(self.src / "day2.jpg", quality=80, exif=_exif(dt="2024:07:05 18:30:00").tobytes())
        self.ingest()
        self.assertEqual(cull(self.conn)["parked"], 0)

    def test_no_capture_time_never_bursts(self):
        from memoryvault.bursts import cull
        base = _scene(10)
        base.save(self.src / "a.jpg")
        base.filter(ImageFilter.GaussianBlur(2)).save(self.src / "b.jpg")
        self.ingest()
        self.assertEqual(cull(self.conn)["parked"], 0)

    def test_different_pictures_at_the_same_moment_are_not_a_burst(self):
        from memoryvault.bursts import cull
        _scene(11).save(self.src / "x.jpg", exif=_exif().tobytes())
        _scene(99).save(self.src / "y.jpg", exif=_exif().tobytes())
        self.ingest()
        self.assertEqual(cull(self.conn)["parked"], 0)

    def test_sharpness_ranks_blur_lower(self):
        from memoryvault.bursts import sharpness
        a = self.tmp / "sharp.jpg"; b = self.tmp / "blur.jpg"
        _scene(12).save(a); _scene(12).filter(ImageFilter.GaussianBlur(3)).save(b)
        self.assertGreater(sharpness(a), sharpness(b) * 2)


if __name__ == "__main__":
    unittest.main()
