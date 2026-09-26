"""Household-specific vault folders come from settings, not code (repo unification).

Pinned: generic default; the setting is honoured (so an existing install keeps
its folders); junk in the setting is dropped; route_to_vault only accepts a
configured folder; the folder list is PIN-gated (a household's folder names
are private) and the three web pages carry no hard-coded folder name.

Run: python3 -m pytest tests/test_vault_folders.py -q
"""
import importlib
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memoryvault import config  # noqa: E402
from memoryvault.constellation import auth  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "memoryvault" / "constellation" / "static"


def _reload_config(**env):
    saved = {k: os.environ.get(k) for k in env}
    try:
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return importlib.reload(config).VAULT_FOLDERS
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(config)


class VaultFoldersTest(unittest.TestCase):
    def test_default_is_generic(self):
        self.assertEqual(_reload_config(MEMORYVAULT_VAULT_FOLDERS=None), ("private", "other"))

    def test_setting_is_honoured_for_existing_installs(self):
        self.assertEqual(_reload_config(MEMORYVAULT_VAULT_FOLDERS="casey,other"), ("casey", "other"))

    def test_junk_entries_are_dropped(self):
        got = _reload_config(MEMORYVAULT_VAULT_FOLDERS=" Alex , ../etc, , other, a b ")
        self.assertEqual(got, ("alex", "other"), "no traversal, no spaces, lowercase")

    def test_route_only_accepts_configured_folders(self):
        import tempfile
        from memoryvault import db, vault
        tmp = Path(tempfile.mkdtemp(prefix="mv-vf-"))
        saved = (config.LIBRARY_ROOT, config.DB_PATH, config.VAULT_MODE, config.VAULT_MOUNT, config.VAULT_FOLDERS)
        config.LIBRARY_ROOT, config.DB_PATH = tmp / "lib", tmp / "lib" / "photos.db"
        config.VAULT_MODE, config.VAULT_MOUNT = "dir", tmp / "vault"
        config.VAULT_FOLDERS = ("alex", "other")
        try:
            conn = db.init(config.DB_PATH)
            (config.LIBRARY_ROOT / "originals").mkdir(parents=True)
            for i, dest in enumerate(("alex", "../escape", "casey")):
                f = config.LIBRARY_ROOT / "originals" / f"p{i}.jpg"
                f.write_bytes(b"\xff\xd8\xff" + bytes([i]) * 64)
                pid = conn.execute(
                    "INSERT INTO photos(sha256, status, library_path, created_at) VALUES (?, 'screened', ?, 'now')",
                    (f"{i:064d}", f"originals/p{i}.jpg")).lastrowid
                vault.route_to_vault(conn, pid, review=False, dest=dest)
            self.assertEqual(len(list((config.VAULT_MOUNT / "alex").iterdir())), 1)
            self.assertFalse((config.VAULT_MOUNT / "casey").exists(), "unconfigured name never becomes a folder")
            self.assertFalse((tmp / "escape").exists())
            self.assertEqual(len([p for p in config.VAULT_MOUNT.iterdir() if p.is_file()]), 2,
                             "unknown destinations fall back to the vault root")
        finally:
            (config.LIBRARY_ROOT, config.DB_PATH, config.VAULT_MODE,
             config.VAULT_MOUNT, config.VAULT_FOLDERS) = saved

    def test_folder_list_is_pin_gated(self):
        self.assertEqual(auth.classify("/api/vault/folders"), auth.PIN)

    def test_pages_carry_no_hard_coded_folder_names(self):
        for page in ("gallery.html", "wall.html", "curation.html"):
            text = (STATIC / page).read_text()
            self.assertNotRegex(text, r"doVault\(\"[a-z]+\"\)|\? \"[a-z]+\"\s*:\s*\(confirm", page)
            self.assertIn("vaultFolders", text + (STATIC / "pin.js").read_text())


if __name__ == "__main__":
    unittest.main()
