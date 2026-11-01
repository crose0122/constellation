"""Supplemental synthetic stat-EIO probes at the directory-barrier guards."""
import json
import os
from pathlib import Path
from unittest import mock
from test_vault_hardening import _Lib, sha
from memoryvault import config, vault


class MetadataUncertaintyTest(_Lib):
    def test_source_retry_does_not_forget_barrier_on_stat_eio(self):
        pid, src = self.add()
        real_fs = vault._fsync_dir
        def fs(d):
            if Path(d) == src.parent:
                raise vault.DurabilityError(5, 'persistent parent fsync EIO')
            return real_fs(d)
        with mock.patch.object(vault, '_fsync_dir', fs):
            with self.assertRaises(vault.VaultCleanupIncomplete):
                vault.route_to_vault(self.conn, pid, False)
            self.assertFalse(src.exists())
            self.assertTrue(src.parent.is_dir())
            real_stat = os.stat
            failures = []
            def stat(p, *a, **k):
                if p == src.parent:
                    failures.append(str(p))
                    raise OSError(5, 'injected parent stat EIO')
                return real_stat(p, *a, **k)
            with mock.patch.object(os, 'stat', stat):
                out = vault.recover_interrupted(self.conn)
            journals = list((config.VAULT_MOUNT / '.pending').glob('*.json'))
            print('STAT_EIO_SOURCE', {'out': out, 'stat_errors': failures, 'journals': list(map(str, journals)), 'parent_exists': src.parent.is_dir()}, flush=True)
            self.assertTrue(failures)
            self.assertEqual(out['completed'], 0, 'metadata uncertainty forgot failed barrier')
            self.assertTrue(journals)

    def test_rollback_retry_does_not_forget_barrier_on_stat_eio(self):
        pid, src = self.add()
        s = sha(src)
        config.VAULT_MOUNT.mkdir(parents=True)
        pending = config.VAULT_MOUNT / '.pending'
        pending.mkdir()
        jp = pending / (s + '.json')
        # Previous rollback already removed the vault copy, then failed fsync.
        private = config.VAULT_MOUNT / 'previous-destination'
        private.mkdir()
        jp.write_text(json.dumps({'sha256': s, 'photo_id': pid, 'source_path': str(src.relative_to(config.LIBRARY_ROOT)), 'vault_path': 'previous-destination/p.jpg'}))
        real_stat = os.stat
        failures = []
        def stat(p, *a, **k):
            if p == private:
                failures.append(str(p))
                raise OSError(5, 'injected rollback directory stat EIO')
            return real_stat(p, *a, **k)
        with mock.patch.object(os, 'stat', stat):
            out = vault.recover_interrupted(self.conn)
        print('STAT_EIO_ROLLBACK', {'out': out, 'stat_errors': failures, 'journal_exists': jp.exists(), 'parent_exists': private.is_dir()}, flush=True)
        self.assertTrue(failures)
        self.assertEqual(out['rolled_back'], 0, 'metadata uncertainty forgot rollback barrier')
        self.assertTrue(jp.exists())
