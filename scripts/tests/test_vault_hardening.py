"""Vault-wall hardening: encrypted backing, no scrub without a verified copy,
crash-safe move/scrub ordering, and a full derivative/metadata audit.

Pinned:
  * luks mode accepts a mountpoint ONLY when its block device is dm-crypt
    (directly or under LVM) — a plain mount, tmpfs or bind mount is refused;
  * a missing library file or a vault copy that doesn't hash to the photo's
    sha256 leaves the DB and library untouched (nothing scrubbed);
  * a DB failure mid-scrub rolls back: vault copy removed, original intact;
  * a REAL hard crash (os._exit in a child process) before or after the
    commit is undone / finished by recover_interrupted();
  * recovery never deletes a library file without a verified vault copy;
  * captions, OCR, placards, errors, duplicate groups, face bans, notes,
    renditions and video frame caches don't survive vaulting; a HEIC's
    untouched original is what goes into the vault.

Run: python3 -m pytest tests/test_vault_hardening.py -q
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from memoryvault import config, db, vault  # noqa: E402


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class _Lib(unittest.TestCase):
    """A throwaway library + dir-mode vault + Obsidian root."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mv-vh-"))
        self.saved = (config.LIBRARY_ROOT, config.DB_PATH, config.VAULT_MODE,
                      config.VAULT_MOUNT, config.MEMORYVAULT_ROOT,
                      config.VAULT_FOLDERS)
        config.LIBRARY_ROOT = self.tmp / "lib"
        config.DB_PATH = self.tmp / "lib" / "photos.db"
        config.VAULT_MODE, config.VAULT_MOUNT = "dir", self.tmp / "vault"
        config.MEMORYVAULT_ROOT = self.tmp / "notes"
        config.VAULT_FOLDERS = ("private", "other")
        self.conn = db.init(config.DB_PATH)

    def tearDown(self):
        self.conn.close()
        (config.LIBRARY_ROOT, config.DB_PATH, config.VAULT_MODE,
         config.VAULT_MOUNT, config.MEMORYVAULT_ROOT,
         config.VAULT_FOLDERS) = self.saved

    def add(self, name="p.jpg", data=None, **cols) -> tuple[int, Path]:
        f = config.LIBRARY_ROOT / "originals" / "2021" / "05" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data or (b"\xff\xd8\xff" + os.urandom(256)))
        row = {"sha256": sha(f), "status": "screened",
               "library_path": str(f.relative_to(config.LIBRARY_ROOT)),
               "original_path": str(f.relative_to(config.LIBRARY_ROOT)),
               "taken_at": "2021-05-01T10:00:00", "created_at": "now"}
        row.update(cols)
        keys = ",".join(row)
        pid = self.conn.execute(
            f"INSERT INTO photos({keys}) VALUES ({','.join('?' * len(row))})",
            tuple(row.values())).lastrowid
        self.conn.execute("INSERT INTO tags VALUES (?, 'people', 'alex', 1, 't')",
                          (pid,))
        self.conn.commit()
        return pid, f

    def vault_files(self):
        return sorted(p.relative_to(config.VAULT_MOUNT).as_posix()
                      for p in config.VAULT_MOUNT.rglob("*") if p.is_file())

    def count(self, sql, *a):
        return self.conn.execute(sql, a).fetchone()[0]


class EncryptedBackingTest(unittest.TestCase):
    """luks mode must prove dm-crypt, not just 'something is mounted'."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mv-luks-"))
        self.mount = self.tmp / "my vault"          # space: mountinfo escapes
        self.mount.mkdir()
        self.sysblk = self.tmp / "sys"
        self.mountinfo = self.tmp / "mountinfo"
        self.saved = (config.VAULT_MODE, config.VAULT_MOUNT,
                      vault.MOUNTINFO, vault.SYS_DEV_BLOCK)
        config.VAULT_MODE, config.VAULT_MOUNT = "luks", self.mount
        vault.MOUNTINFO, vault.SYS_DEV_BLOCK = self.mountinfo, self.sysblk
        self.ismount = mock.patch.object(vault.os.path, "ismount",
                                         lambda p: Path(p) == self.mount)
        self.ismount.start()

    def tearDown(self):
        self.ismount.stop()
        (config.VAULT_MODE, config.VAULT_MOUNT,
         vault.MOUNTINFO, vault.SYS_DEV_BLOCK) = self.saved

    def _dev(self, majmin, uuid=None, slaves=()):
        d = self.sysblk / majmin
        (d / "slaves").mkdir(parents=True, exist_ok=True)
        if uuid is not None:
            (d / "dm").mkdir(exist_ok=True)
            (d / "dm" / "uuid").write_text(uuid + "\n")
        for s in slaves:
            sd = d / "slaves" / f"dev{s.replace(':', '_')}"
            sd.mkdir()
            (sd / "dev").write_text(s + "\n")

    def _mounted_on(self, majmin):
        esc = str(self.mount).replace(" ", "\\040")
        self.mountinfo.write_text(
            "22 1 259:2 / / rw - ext4 /dev/nvme0n1p2 rw\n"
            f"98 22 {majmin} / {esc} rw,relatime - ext4 /dev/x rw\n")

    def test_plain_block_device_mount_is_refused(self):
        self._dev("8:17")                               # a USB stick
        self._mounted_on("8:17")
        self.assertFalse(vault.is_mounted())

    def test_dm_but_not_crypt_is_refused(self):
        self._dev("253:1", uuid="LVM-abcdef")           # plain LVM volume
        self._mounted_on("253:1")
        self.assertFalse(vault.is_mounted())

    def test_luks_mapper_is_accepted(self):
        self._dev("253:0", uuid="CRYPT-LUKS2-0123abcd-memoryvault")
        self._mounted_on("253:0")
        self.assertTrue(vault.is_mounted())

    def test_lvm_on_luks_is_accepted(self):
        self._dev("253:0", uuid="CRYPT-LUKS2-0123abcd-root")
        self._dev("253:1", uuid="LVM-xyz", slaves=["253:0"])
        self._mounted_on("253:1")
        self.assertTrue(vault.is_mounted())

    def test_topmost_mount_wins(self):
        self._dev("253:0", uuid="CRYPT-LUKS2-0123abcd-memoryvault")
        self._dev("0:55")                               # tmpfs stacked on top
        self._mounted_on("253:0")
        esc = str(self.mount).replace(" ", "\\040")
        with open(self.mountinfo, "a") as f:
            f.write(f"99 98 0:55 / {esc} rw - tmpfs tmpfs rw\n")
        self.assertFalse(vault.is_mounted(), "writes would land on the tmpfs")

    def test_not_a_mountpoint_or_unreadable_mountinfo_is_refused(self):
        self._dev("253:0", uuid="CRYPT-LUKS2-0123abcd-memoryvault")
        self.assertFalse(vault.is_mounted(), "no mountinfo file at all")
        self._mounted_on("253:0")
        with mock.patch.object(vault.os.path, "ismount", lambda p: False):
            self.assertFalse(vault.is_mounted())


class NoScrubWithoutVerifiedCopyTest(_Lib):
    def test_missing_source_leaves_db_intact(self):
        pid, f = self.add()
        f.unlink()
        with self.assertRaises(vault.VaultSourceMissing):
            vault.route_to_vault(self.conn, pid, review=False)
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos WHERE id=?", pid), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM tags WHERE photo_id=?", pid), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM vaulted"), 0)
        self.assertEqual(self.vault_files(), [])

    def test_sha_mismatch_scrubs_nothing(self):
        pid, f = self.add()
        self.conn.execute("UPDATE photos SET sha256=? WHERE id=?", ("0" * 64, pid))
        self.conn.commit()
        with self.assertRaises(vault.VaultIntegrityError):
            vault.route_to_vault(self.conn, pid, review=False)
        self.assertTrue(f.is_file())
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 1)
        self.assertEqual(self.vault_files(), [], "no .part, no journal, no copy")

    def test_db_failure_rolls_back_copy_and_keeps_original(self):
        pid, f = self.add()
        boom = sqlite3.OperationalError("database is locked")
        with mock.patch.object(vault, "_scrub_db", side_effect=boom):
            with self.assertRaises(sqlite3.OperationalError):
                vault.route_to_vault(self.conn, pid, review=True)
        self.assertTrue(f.is_file())
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM vaulted"), 0)
        self.assertEqual(self.vault_files(), [])
        # and the retry (what screen's lock-retry does) then succeeds cleanly
        vault.route_to_vault(self.conn, pid, review=True)
        self.assertFalse(f.exists())
        self.assertEqual(len(self.vault_files()), 1)
        self.assertTrue(self.vault_files()[0].startswith("review/"))

    def test_partial_rollback_inside_scrub_is_atomic(self):
        # first DELETEs succeed, then one fails: none of them may stick
        pid, f = self.add()
        real = vault.bump_stat

        def fail(*a, **k):
            raise sqlite3.IntegrityError("injected after the deletes")
        with mock.patch.object(vault, "bump_stat", side_effect=fail):
            with self.assertRaises(sqlite3.IntegrityError):
                vault.route_to_vault(self.conn, pid, review=False)
        self.assertIs(vault.bump_stat, real)
        self.assertEqual(self.count("SELECT COUNT(*) FROM tags WHERE photo_id=?", pid), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 1)
        self.assertTrue(f.is_file())


CHILD = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, sys.argv[1])
    from memoryvault import config, db, vault
    point = sys.argv[3]
    if point == "before_commit":
        vault._scrub_db = lambda *a, **k: os._exit(9)
    else:
        vault._cleanup_library = lambda *a, **k: os._exit(9)
    conn = db.connect(config.DB_PATH)
    vault.route_to_vault(conn, int(sys.argv[2]), review=False)
    os._exit(0)
""")


class HardCrashRecoveryTest(_Lib):
    def crash(self, pid, point):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "PYTHONHOME")}
        env.update(MEMORYVAULT_LIBRARY_ROOT=str(config.LIBRARY_ROOT),
                   MEMORYVAULT_DB_PATH=str(config.DB_PATH),
                   MEMORYVAULT_VAULT_MODE="dir",
                   MEMORYVAULT_VAULT_MOUNT=str(config.VAULT_MOUNT),
                   MEMORYVAULT_ROOT=str(config.MEMORYVAULT_ROOT))
        r = subprocess.run([sys.executable, "-c", CHILD, str(HERE), str(pid),
                            point], env=env, capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 9, r.stderr.decode())

    def test_crash_before_commit_is_rolled_back(self):
        pid, f = self.add()
        self.crash(pid, "before_commit")
        # the real on-disk state after a kill: copy in vault, DB untouched
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 1)
        self.assertTrue(any(p.startswith(".pending/") for p in self.vault_files()))
        out = vault.recover_interrupted(self.conn)
        self.assertEqual(out, {"completed": 0, "rolled_back": 1,
                               "needs_attention": []})
        self.assertTrue(f.is_file())
        self.assertEqual(self.vault_files(), [], "orphan copy + journal gone")
        vault.route_to_vault(self.conn, pid, review=False)   # retry works
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 0)
        self.assertFalse(f.exists())

    def test_crash_after_commit_is_finished(self):
        pid, f = self.add()
        thumb = config.LIBRARY_ROOT / "thumbnails" / f"{sha(f)[:16]}.jpg"
        thumb.parent.mkdir(parents=True)
        thumb.write_bytes(b"t")
        self.crash(pid, "after_commit")
        # committed scrub, but the plaintext original is still on disk
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 0)
        self.assertTrue(f.is_file() and thumb.is_file())
        out = vault.recover_interrupted(self.conn)
        self.assertEqual(out["completed"], 1)
        self.assertFalse(f.exists())
        self.assertFalse(thumb.exists())
        files = self.vault_files()
        self.assertEqual(len(files), 1)
        self.assertFalse(files[0].startswith(".pending"))

    def test_recovery_never_deletes_without_a_verified_vault_copy(self):
        pid, f = self.add()
        self.crash(pid, "after_commit")
        j = json.loads(next((config.VAULT_MOUNT / ".pending").glob("*.json")).read_text())
        vcopy = config.VAULT_MOUNT / j["vault_path"]
        vcopy.write_bytes(b"corrupted in the vault")
        out = vault.recover_interrupted(self.conn)
        self.assertEqual(out["completed"], 0)
        self.assertEqual(len(out["needs_attention"]), 1)
        self.assertTrue(f.is_file(), "the library file may be the only good copy")

    def test_source_vanished_mid_move_rolls_forward(self):
        pid, f = self.add()
        self.crash(pid, "before_commit")
        f.unlink()                     # e.g. the disk lost it after the copy
        out = vault.recover_interrupted(self.conn)
        self.assertEqual(out["completed"], 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 0,
                         "vault copy verified -> scrub completes")
        self.assertEqual(self.count("SELECT COUNT(*) FROM vaulted"), 1)


class DerivativeAuditTest(_Lib):
    def test_every_trace_goes(self):
        pid, f = self.add(name="beach.jpg")
        other, _ = self.add(name="keep.jpg")
        s = sha(f)
        c = self.conn
        c.execute("INSERT INTO descriptions(photo_id, caption, ocr_text) "
                  "VALUES (?, 'secret caption', 'secret ocr')", (pid,))
        c.execute("CREATE TABLE IF NOT EXISTS placards (photo_id INTEGER "
                  "PRIMARY KEY, placard TEXT, model_version TEXT)")
        c.execute("INSERT INTO placards VALUES (?, 'secret placard', 'x')", (pid,))
        db.record_error(c, "describe", "boom on beach.jpg", photo_id=pid)
        src_id = c.execute("INSERT INTO sources(kind, root) VALUES ('dir', '/x')").lastrowid
        fid = c.execute("INSERT INTO files(photo_id, source_id, source_path, "
                        "disposition, discovered_at) VALUES (?, ?, '/x/beach.jpg', "
                        "'canonical', 'now')", (pid, src_id)).lastrowid
        gid = c.execute("INSERT INTO duplicate_groups(kind, keeper_photo_id) "
                        "VALUES ('exact', ?)", (pid,)).lastrowid
        c.execute("INSERT INTO duplicate_members(group_id, file_id) VALUES (?, ?)",
                  (gid, fid))
        face = c.execute("INSERT INTO faces(photo_id, bbox, embedding) VALUES "
                         "(?, '[]', x'00')", (pid,)).lastrowid
        c.execute("INSERT INTO face_bans VALUES (?, 'alex')", (face,))
        c.execute("INSERT INTO photo_edges VALUES (?, ?, 'near', 1)", (pid, other))
        c.execute("INSERT INTO embeddings VALUES (?, 'm', x'00')", (pid,))
        c.commit()
        L = config.LIBRARY_ROOT
        derived = [L / "thumbnails" / f"{s[:16]}.jpg", L / "display" / f"{s[:16]}.jpg",
                   L / "face-crops" / f"{face}.jpg",
                   L / "staging" / f"screenframes-{s[:16]}" / "0.jpg",
                   L / "staging" / f"capframes-{s[:16]}" / "0.jpg"]
        for d in derived:
            d.parent.mkdir(parents=True, exist_ok=True)
            d.write_bytes(b"x")
        note = config.MEMORYVAULT_ROOT / "Photos" / "2021" / "beach.md"
        note.parent.mkdir(parents=True)
        note.write_text("---\ngenerated: true\nphoto: originals/2021/05/beach.jpg\n"
                        "---\n<!-- manual -->our trip<!-- /manual -->\n")
        idx = config.MEMORYVAULT_ROOT / "People" / "alex.md"
        idx.parent.mkdir(parents=True)
        idx.write_text("---\ntype: index\n---\n- [[Photos/2021/beach]] (2021)\n"
                       "- [[Photos/2021/keep]] (2021)\n")

        vault.route_to_vault(c, pid, review=False, dest="private")

        for tbl, col in (("photos", "id"), ("tags", "photo_id"),
                         ("descriptions", "photo_id"), ("placards", "photo_id"),
                         ("errors", "photo_id"), ("files", "photo_id"),
                         ("duplicate_groups", "keeper_photo_id"),
                         ("faces", "photo_id"), ("embeddings", "photo_id")):
            self.assertEqual(self.count(f"SELECT COUNT(*) FROM {tbl} WHERE {col}=?", pid),
                             0, tbl)
        self.assertEqual(self.count("SELECT COUNT(*) FROM duplicate_members"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM face_bans"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM photo_edges"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM vaulted WHERE sha256=?", s), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos WHERE id=?", other), 1)
        self.assertFalse(f.exists())
        for d in derived:
            self.assertFalse(d.exists(), d)
        self.assertFalse(note.exists())
        self.assertIn("our trip", (config.VAULT_MOUNT / ".notes" / f"{s[:16]}.md").read_text(),
                      "a human-written note moves into the vault, not the bin")
        self.assertNotIn("beach", idx.read_text())
        self.assertIn("keep", idx.read_text())
        self.assertEqual(self.vault_files(), [f".notes/{s[:16]}.md", "private/beach.jpg"])
        # nothing secret left in the DB file bytes after a checkpoint
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        raw = config.DB_PATH.read_bytes()
        for secret in (b"secret caption", b"secret ocr", b"secret placard",
                       b"/x/beach.jpg", b"boom on beach.jpg"):
            self.assertNotIn(secret, raw, secret)

    def test_heic_original_is_vaulted_and_rendition_removed(self):
        heic = config.LIBRARY_ROOT / "originals" / "2021" / "05" / "IMG_1.HEIC"
        heic.parent.mkdir(parents=True)
        heic.write_bytes(b"\x00\x00\x00\x18ftypheic" + os.urandom(200))
        rend = config.LIBRARY_ROOT / "renditions" / "2021" / "05" / "IMG_1.jpg"
        rend.parent.mkdir(parents=True)
        rend.write_bytes(b"\xff\xd8\xff" + os.urandom(100))
        pid = self.conn.execute(
            "INSERT INTO photos(sha256, status, library_path, original_path, "
            "created_at) VALUES (?, 'screened', ?, ?, 'now')",
            (sha(heic), "renditions/2021/05/IMG_1.jpg",
             "originals/2021/05/IMG_1.HEIC")).lastrowid
        self.conn.commit()
        vault.route_to_vault(self.conn, pid, review=False)
        self.assertFalse(heic.exists(), "the original must not stay in plaintext")
        self.assertFalse(rend.exists())
        self.assertEqual(self.vault_files(), ["IMG_1.HEIC"])
        self.assertEqual(sha(config.VAULT_MOUNT / "IMG_1.HEIC"),
                         self.conn.execute("SELECT sha256 FROM vaulted").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
