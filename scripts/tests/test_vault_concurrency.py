"""Vault mover/recovery exclusion and durability ordering.

An adversarial review reproduced total photo loss: recovery running
on a second connection between a live mover's verified rename and its scrub
read the live journal as "died before commit", deleted the vault copy, and
the mover then scrubbed the DB and deleted the original. These tests pin:

  * one cross-process lock (vault_lock) serialises every mover and recovery;
    recovery against a held lock touches NOTHING and reports busy;
  * a real second PROCESS running recovery mid-move cannot roll it back;
  * two movers of the same photo serialise to exactly one vault copy;
  * a stale journal for the same bytes is settled before a new move;
  * publication never overwrites a file that appeared at the destination;
  * durability barriers fail closed: a failed fsync before the scrub rolls
    back; after it, the journal is kept until cleanup is durable;
  * the vaulted Obsidian note is durable before the original is unlinked,
    and plaintext directories are fsynced before the journal is retired.

Power-cut write ordering on real disks is NOT proven here (no power cut
rig); these pin the code's barrier ORDER and fail-closed behaviour.

Run: python3 -m pytest tests/test_vault_concurrency.py -q
"""
import os
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from memoryvault import config, db, vault  # noqa: E402
from test_vault_hardening import _Lib, sha  # noqa: E402

SLOW_MOVER = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, sys.argv[1])
    from memoryvault import config, db, vault
    ready, hold = sys.argv[3], float(sys.argv[4])
    real = vault._scrub_db
    def slow(*a, **k):
        open(ready, "w").close()      # verified copy published, scrub next
        time.sleep(hold)
        return real(*a, **k)
    vault._scrub_db = slow
    conn = db.connect(config.DB_PATH)
    vault.route_to_vault(conn, int(sys.argv[2]), review=False)
    conn.close()
""")


class _Child(_Lib):
    def env(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "PYTHONHOME")}
        env.update(MEMORYVAULT_LIBRARY_ROOT=str(config.LIBRARY_ROOT),
                   MEMORYVAULT_DB_PATH=str(config.DB_PATH),
                   MEMORYVAULT_VAULT_MODE="dir",
                   MEMORYVAULT_VAULT_MOUNT=str(config.VAULT_MOUNT),
                   MEMORYVAULT_ROOT=str(config.MEMORYVAULT_ROOT))
        return env

    def slow_mover(self, pid, ready, hold=1.5):
        return subprocess.Popen(
            [sys.executable, "-c", SLOW_MOVER, str(HERE), str(pid), str(ready),
             str(hold)], env=self.env(), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)

    def wait_for(self, path, secs=30):
        end = time.monotonic() + secs
        while not Path(path).exists():
            self.assertLess(time.monotonic(), end, f"{path} never appeared")
            time.sleep(0.02)


class MoverRecoveryExclusionTest(_Child):
    def test_inline_recovery_during_live_move_touches_nothing(self):
        # Deterministic interleaving from the adversarial reproducer:
        # recovery on a second connection right before the mover's scrub.
        pid, original = self.add()
        other = db.connect(config.DB_PATH)
        real_scrub = vault._scrub_db
        seen = {}

        def recover_before_scrub(*a, **k):
            seen["before"] = self.vault_files()
            seen["recovery"] = vault.recover_interrupted(other)
            seen["after"] = self.vault_files()
            return real_scrub(*a, **k)

        try:
            with mock.patch.object(vault, "RECOVERY_LOCK_TIMEOUT", 0.2), \
                    mock.patch.object(vault, "_scrub_db",
                                      side_effect=recover_before_scrub):
                vault.route_to_vault(self.conn, pid, review=False)
        finally:
            other.close()
        self.assertTrue(seen["recovery"].get("busy"))
        self.assertEqual(seen["recovery"]["rolled_back"], 0)
        self.assertEqual(seen["before"], seen["after"], "recovery touched files")
        self.assertFalse(original.exists())
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 0)
        self.assertEqual(self.vault_files(), ["p.jpg"])

    def test_recovery_in_another_process_cannot_roll_back_a_live_move(self):
        pid, original = self.add()
        ready = self.tmp / "ready"
        child = self.slow_mover(pid, ready)
        try:
            self.wait_for(ready)
            mid = self.vault_files()
            self.assertIn("p.jpg", mid)
            busy = vault.recover_interrupted(self.conn, timeout=0.2)
            self.assertEqual(busy, {"completed": 0, "rolled_back": 0,
                                    "needs_attention": [], "busy": True})
            self.assertEqual(self.vault_files(), mid)
            self.assertTrue(original.exists(), "nothing deleted mid-move")
            # a patient recovery waits for the mover, then finds nothing dead
            out = vault.recover_interrupted(self.conn, timeout=30)
            self.assertNotIn("busy", out)
            self.assertEqual(out["rolled_back"], 0)
        finally:
            _, err = child.communicate(timeout=60)
        self.assertEqual(child.returncode, 0, err.decode())
        self.assertFalse(original.exists())
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 0)
        self.assertEqual(self.vault_files(), ["p.jpg"])
        self.assertEqual(sha(config.VAULT_MOUNT / "p.jpg"),
                         self.conn.execute("SELECT sha256 FROM vaulted")
                         .fetchone()[0])

    def test_two_movers_of_the_same_photo_serialise(self):
        pid, original = self.add()
        r1, r2 = self.tmp / "r1", self.tmp / "r2"
        a = self.slow_mover(pid, r1, hold=0.8)
        self.wait_for(r1)
        b = self.slow_mover(pid, r2, hold=0.1)     # blocks on the lock
        for p in (a, b):
            _, err = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, err.decode())
        self.assertFalse(r2.exists(), "second mover saw the row gone, no copy")
        self.assertEqual(self.vault_files(), ["p.jpg"])
        self.assertFalse(original.exists())
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 0)

    def test_mover_waits_then_gives_up_without_touching_anything(self):
        pid, original = self.add()
        with mock.patch.object(vault, "LOCK_TIMEOUT", 0.2):
            with vault.vault_lock():
                with self.assertRaises(vault.VaultBusy):
                    vault.route_to_vault(self.conn, pid, review=False)
        self.assertTrue(original.exists())
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 1)
        self.assertEqual(self.vault_files(), [], "no journal, no copy")
        vault.route_to_vault(self.conn, pid, review=False)   # then it works
        self.assertEqual(self.vault_files(), ["p.jpg"])

    def test_review_verbs_take_the_lock(self):
        (config.VAULT_MOUNT / "review").mkdir(parents=True)
        (config.VAULT_MOUNT / "review" / "x.jpg").write_bytes(b"x")
        with mock.patch.object(vault, "LOCK_TIMEOUT", 0.2):
            with vault.vault_lock():
                with self.assertRaises(vault.VaultBusy):
                    vault.keep_in_vault("x.jpg")
                with self.assertRaises(vault.VaultBusy):
                    vault.delete_from_review("x.jpg")
                with self.assertRaises(vault.VaultBusy):
                    vault.release_from_review(self.conn, "x.jpg")
        self.assertEqual(self.vault_files(), ["review/x.jpg"])


class StaleJournalAndCollisionTest(_Child):
    def test_stale_journal_for_same_bytes_is_settled_first(self):
        pid, original = self.add()
        # a mover that died before commit: journal + published copy
        child = subprocess.run(
            [sys.executable, "-c", textwrap.dedent("""
                import os, sys
                sys.path.insert(0, sys.argv[1])
                from memoryvault import config, db, vault
                vault._scrub_db = lambda *a, **k: os._exit(9)
                vault.route_to_vault(db.connect(config.DB_PATH),
                                     int(sys.argv[2]), review=False)
            """), str(HERE), str(pid)], env=self.env(), capture_output=True,
            timeout=60)
        self.assertEqual(child.returncode, 9, child.stderr.decode())
        self.assertTrue(any(p.startswith(".pending/") for p in self.vault_files()))
        vault.route_to_vault(self.conn, pid, review=False)
        self.assertEqual(self.vault_files(), ["p.jpg"],
                         "one copy, no orphan, journal retired")
        self.assertFalse(original.exists())

    def test_publish_never_overwrites_a_file_that_appeared(self):
        pid, original = self.add()
        dst = config.VAULT_MOUNT / "p.jpg"
        real_copy = vault.shutil.copyfile

        def copy_then_collide(a, b, *k, **kw):
            r = real_copy(a, b, *k, **kw)
            dst.write_bytes(b"someone else's vault file")
            return r
        with mock.patch.object(vault.shutil, "copyfile", copy_then_collide):
            with self.assertRaises(FileExistsError):
                vault.route_to_vault(self.conn, pid, review=False)
        self.assertEqual(dst.read_bytes(), b"someone else's vault file")
        self.assertTrue(original.exists())
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM vaulted"), 0)
        self.assertEqual(self.vault_files(), ["p.jpg"], "no journal left")


class DurabilityBarrierTest(_Lib):
    def test_fsync_dir_fails_closed(self):
        d = self.tmp / "d"
        d.mkdir()
        with mock.patch.object(vault.os, "fsync", side_effect=OSError(5, "EIO")):
            with self.assertRaises(vault.DurabilityError):
                vault._fsync_dir(d)
        with self.assertRaises(vault.DurabilityError):
            vault._fsync_dir(self.tmp / "missing")

    def test_barrier_failure_after_publish_rolls_back_before_scrub(self):
        pid, original = self.add()
        state = {"published": False}
        real_pub, real_fs = vault._publish_no_overwrite, vault._fsync_dir

        def pub(*a):
            real_pub(*a)
            state["published"] = True

        def fs(d):
            if state["published"]:
                raise vault.DurabilityError(5, "injected")
            return real_fs(d)
        with mock.patch.object(vault, "_publish_no_overwrite", pub), \
                mock.patch.object(vault, "_fsync_dir", fs):
            with self.assertRaises(vault.DurabilityError):
                vault.route_to_vault(self.conn, pid, review=False)
        self.assertTrue(original.exists())
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM tags"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM vaulted"), 0)
        # the copy is gone; the rollback's own barrier also failed, so the
        # journal stays for recovery rather than being retired un-durably
        self.assertEqual([f for f in self.vault_files()
                          if not f.startswith(".pending/")], [])
        out = vault.recover_interrupted(self.conn)
        self.assertEqual(out["rolled_back"], 1)
        self.assertEqual(self.vault_files(), [])
        self.assertTrue(original.exists())

    def test_journal_write_barrier_failure_touches_nothing(self):
        pid, original = self.add()
        (config.VAULT_MOUNT / ".pending").mkdir(parents=True)
        real_fs = vault._fsync_dir

        def fs(d):
            if Path(d).name == ".pending":
                raise vault.DurabilityError(5, "injected")
            return real_fs(d)
        with mock.patch.object(vault, "_fsync_dir", fs):
            with self.assertRaises(vault.DurabilityError):
                vault.route_to_vault(self.conn, pid, review=False)
        self.assertTrue(original.exists())
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 1)
        self.assertEqual(self.vault_files(), [])

    def test_cleanup_barrier_failure_keeps_journal_until_durable(self):
        pid, original = self.add()
        real_fs = vault._fsync_dir

        def fs(d):
            if Path(d) == original.parent:
                raise vault.DurabilityError(5, "injected")
            return real_fs(d)
        with mock.patch.object(vault, "_fsync_dir", fs):
            with self.assertRaises(vault.VaultCleanupIncomplete):
                vault.route_to_vault(self.conn, pid, review=False)
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 0)
        self.assertTrue(any(p.startswith(".pending/") and p.endswith(".json")
                            for p in self.vault_files()), "journal kept")
        out = vault.recover_interrupted(self.conn)
        self.assertEqual(out["completed"], 1)
        self.assertEqual(self.vault_files(), ["p.jpg"])

    def _note(self):
        note = config.MEMORYVAULT_ROOT / "Photos" / "2021" / "p.md"
        note.parent.mkdir(parents=True)
        note.write_text("---\nphoto: originals/2021/05/p.jpg\n---\n"
                        "<!-- manual -->grandma's words<!-- /manual -->\n")
        return note

    def test_note_publication_failure_keeps_the_note(self):
        pid, original = self.add()
        note = self._note()
        real_w = vault._write_durable

        def w(path, data):
            if vault.NOTES_DIR in Path(path).parts:
                raise vault.DurabilityError(5, "injected")
            return real_w(path, data)
        with mock.patch.object(vault, "_write_durable", w):
            with self.assertRaises(vault.VaultCleanupIncomplete):
                vault.route_to_vault(self.conn, pid, review=False)
        self.assertTrue(note.exists(), "the only copy of human text survives")
        self.assertIn("grandma's words", note.read_text())
        out = vault.recover_interrupted(self.conn)
        self.assertEqual(out["completed"], 1)
        self.assertFalse(note.exists())
        s = sha(config.VAULT_MOUNT / "p.jpg")
        self.assertIn("grandma's words",
                      (config.VAULT_MOUNT / ".notes" / f"{s[:16]}.md").read_text())

    def test_barrier_order_note_before_unlink_dirs_before_journal(self):
        pid, original = self.add()
        note = self._note()
        events = []
        real_fs, real_w = vault._fsync_dir, vault._write_durable
        real_unlink = Path.unlink

        def fs(d):
            events.append(("fsync", Path(d)))
            return real_fs(d)

        def w(path, data):
            r = real_w(path, data)
            events.append(("durable", Path(path)))
            return r

        def unlink(self_, *a, **k):
            events.append(("unlink", Path(self_)))
            return real_unlink(self_, *a, **k)
        with mock.patch.object(vault, "_fsync_dir", fs), \
                mock.patch.object(vault, "_write_durable", w), \
                mock.patch.object(Path, "unlink", unlink):
            vault.route_to_vault(self.conn, pid, review=False)

        def at(kind, pred):
            return next(i for i, e in enumerate(events)
                        if e[0] == kind and pred(e[1]))
        note_copy = at("durable", lambda p: vault.NOTES_DIR in p.parts)
        note_gone = at("unlink", lambda p: p == note)
        self.assertLess(note_copy, note_gone)
        journal_gone = at("unlink", lambda p: p.suffix == ".json")
        for d in (original.parent, note.parent):
            self.assertLess(at("fsync", lambda p, d=d: p == d), journal_gone, d)
        self.assertLess(at("unlink", lambda p: p == original),
                        at("fsync", lambda p: p == original.parent))


class RetryDurabilityTest(_Lib):
    """A failed directory barrier must
    be RETRIED by recovery, even when the file it covered is already gone or
    the directory it covered already exists. Failures here are persistent
    (installed across both the mover and the recovery pass)."""

    def _persistent(self, bad):
        real_fs = vault._fsync_dir
        attempts = []

        def fs(d):
            attempts.append(Path(d))
            if bad(Path(d)):
                raise vault.DurabilityError(5, "persistent EIO")
            return real_fs(d)
        return mock.patch.object(vault, "_fsync_dir", fs), attempts

    def journals(self):
        return list((config.VAULT_MOUNT / ".pending").glob("*.json"))

    def test_recovery_retries_failed_unlink_directory_barrier(self):
        # Adopted from the adversarial reproducer
        pid, original = self.add()
        patch, attempts = self._persistent(lambda d: d == original.parent)
        with patch:
            with self.assertRaises(vault.VaultCleanupIncomplete):
                vault.route_to_vault(self.conn, pid, review=False)
            self.assertFalse(original.exists())
            self.assertTrue(self.journals())
            attempts.clear()
            out = vault.recover_interrupted(self.conn)
            self.assertEqual(out["completed"], 0)
            self.assertIn(original.parent, attempts, "barrier retried")
            self.assertTrue(self.journals(), "journal kept while it fails")
        out = vault.recover_interrupted(self.conn)      # disk healthy again
        self.assertEqual(out["completed"], 1)
        self.assertEqual(self.journals(), [])

    def test_notes_directory_parent_barrier_is_retried_before_note_unlink(self):
        # Adopted from the adversarial reproducer
        pid, original = self.add()
        note = config.MEMORYVAULT_ROOT / "Photos" / "2021" / "p.md"
        note.parent.mkdir(parents=True)
        note.write_text("---\nphoto: originals/2021/05/p.jpg\n---\n"
                        "manual unique text\n")
        nd = config.VAULT_MOUNT / vault.NOTES_DIR
        patch, attempts = self._persistent(
            lambda d: d == config.VAULT_MOUNT and nd.is_dir())
        with patch:
            with self.assertRaises(vault.VaultCleanupIncomplete):
                vault.route_to_vault(self.conn, pid, review=False)
            self.assertTrue(nd.is_dir())
            self.assertTrue(note.exists())
            attempts.clear()
            out = vault.recover_interrupted(self.conn)
            self.assertIn(config.VAULT_MOUNT, attempts, "mkdir barrier retried")
            self.assertTrue(note.exists(), "only durable manual text kept")
            self.assertEqual(out["completed"], 0)
            self.assertTrue(self.journals())
        out = vault.recover_interrupted(self.conn)
        self.assertEqual(out["completed"], 1)
        self.assertFalse(note.exists())
        self.assertIn("manual unique text",
                      (nd / f"{sha(config.VAULT_MOUNT / 'p.jpg')[:16]}.md")
                      .read_text())

    def test_existing_directory_still_gets_its_parent_barrier(self):
        d = self.tmp / "exists"
        d.mkdir()
        with mock.patch.object(vault, "_fsync_dir") as fs:
            vault._mkdir_durable(d)
        fs.assert_called_once_with(d.parent)

    def test_rollback_retries_vault_copy_barrier_after_copy_is_gone(self):
        # rollback unlinked the copy, then its directory barrier failed;
        # the retry finds no copy but must still repeat that barrier
        pid, original = self.add()
        state = {"published": False}
        real_pub = vault._publish_no_overwrite

        def pub(*a):
            real_pub(*a)
            state["published"] = True
        patch, attempts = self._persistent(
            lambda d: state["published"] and d == config.VAULT_MOUNT)
        with patch, mock.patch.object(vault, "_publish_no_overwrite", pub):
            with self.assertRaises(vault.DurabilityError):
                vault.route_to_vault(self.conn, pid, review=False)
            self.assertNotIn("p.jpg", self.vault_files())
            self.assertTrue(self.journals())
            attempts.clear()
            out = vault.recover_interrupted(self.conn)
            self.assertIn(config.VAULT_MOUNT, attempts)
            self.assertEqual(out["rolled_back"], 0)
            self.assertTrue(self.journals(), "journal kept while it fails")
        out = vault.recover_interrupted(self.conn)
        self.assertEqual(out["rolled_back"], 1)
        self.assertTrue(original.exists())
        self.assertEqual(self.count("SELECT COUNT(*) FROM photos"), 1)

    def test_index_rewrite_barrier_is_retried_after_link_is_gone(self):
        pid, original = self.add()
        note = config.MEMORYVAULT_ROOT / "Photos" / "2021" / "p.md"
        note.parent.mkdir(parents=True)
        note.write_text("---\nphoto: originals/2021/05/p.jpg\n---\n")
        idx = config.MEMORYVAULT_ROOT / "People" / "alex.md"
        idx.parent.mkdir(parents=True)
        idx.write_text("---\ntype: index\n---\n- [[Photos/2021/p]]\n- keep\n")
        patch, attempts = self._persistent(lambda d: d == idx.parent)
        with patch:
            with self.assertRaises(vault.VaultCleanupIncomplete):
                vault.route_to_vault(self.conn, pid, review=False)
            self.assertNotIn("[[Photos/2021/p]]", idx.read_text())
            attempts.clear()
            out = vault.recover_interrupted(self.conn)
            self.assertIn(idx.parent, attempts, "index barrier retried")
            self.assertEqual(out["completed"], 0)
            self.assertTrue(self.journals())
        out = vault.recover_interrupted(self.conn)
        self.assertEqual(out["completed"], 1)


if __name__ == "__main__":
    unittest.main()
