"""Metadata errors are never "file is missing".

Python 3.14's Path.is_file()/is_dir()/exists() turn EIO into False. In the
cleanup path that would mean "the plaintext original is already gone", so
the journal retires while the original may still be on disk. These cases
inject a stat EIO on the original itself and on a derivative and require
the cleanup to REPORT failure (journal kept) instead of shrugging.

They are red on 590b8747's vault.py under Python 3.14 and green on both
3.12 and 3.14 with the strict-stat helpers."""
import errno
import os
from pathlib import Path
from unittest import mock

from test_vault_hardening import _Lib, sha
from memoryvault import config, vault


def _eio_on(target: Path, seen: list):
    real = os.stat

    def stat(p, *a, **k):
        if Path(p) == target:
            seen.append(str(p))
            raise OSError(errno.EIO, "injected stat EIO")
        return real(p, *a, **k)
    return stat


class StrictMetadataTest(_Lib):
    def test_source_stat_eio_keeps_journal_and_original(self):
        _, src = self.add()
        j = {"sha256": sha(src),
             "source_path": str(src.relative_to(config.LIBRARY_ROOT))}
        seen = []
        with mock.patch.object(os, "stat", _eio_on(src, seen)):
            failed = vault._cleanup_library(j)
        self.assertTrue(seen, "probe never reached the original")
        self.assertIn(str(src), failed)
        self.assertTrue(src.is_file())

    def test_derivative_stat_eio_is_reported(self):
        _, src = self.add()
        s = sha(src)
        thumb = config.LIBRARY_ROOT / "thumbnails" / f"{s[:16]}.jpg"
        thumb.parent.mkdir(parents=True)
        thumb.write_bytes(b"thumb")
        src.unlink()
        j = {"sha256": s,
             "source_path": str(src.relative_to(config.LIBRARY_ROOT))}
        seen = []
        with mock.patch.object(os, "stat", _eio_on(thumb, seen)):
            failed = vault._cleanup_library(j)
        self.assertTrue(seen)
        self.assertIn(str(thumb), failed)
        self.assertTrue(thumb.exists())

    def test_missing_paths_are_still_fine(self):
        # control: genuinely absent files must not be reported as failures
        _, src = self.add()
        j = {"sha256": sha(src),
             "source_path": str(src.relative_to(config.LIBRARY_ROOT))}
        src.unlink()
        self.assertEqual(vault._cleanup_library(j), [])
