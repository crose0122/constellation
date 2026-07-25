"""One-time migration of the legacy plaintext Quarantine/ folder into the
LUKS vault (SPEC.md §6): mount → move → shred → verify empty.

The old pipeline moved suspected-explicit photos to an UNENCRYPTED folder;
this closes that gap. Files are copied into the vault, fsynced, then the
plaintext originals are shredded (shred -u, with an overwrite fallback)."""

import os
import shutil
import subprocess
from pathlib import Path

from . import config
from . import vault


def _shred(path: Path):
    try:
        subprocess.run(["shred", "-u", str(path)], check=True,
                       capture_output=True)
        return
    except (FileNotFoundError, subprocess.CalledProcessError):
        # fallback: single-pass overwrite then unlink (better than plain rm)
        size = path.stat().st_size
        with open(path, "r+b") as f:
            f.write(os.urandom(min(size, 1 << 20)) * max(1, size // (1 << 20) + 1))
            f.flush()
            os.fsync(f.fileno())
        path.unlink()


def migrate_quarantine(quarantine_dir: Path | None = None) -> dict:
    qdir = quarantine_dir or (config.MEMORYVAULT_ROOT / "Quarantine")
    if not qdir.exists():
        print(f"nothing to migrate: {qdir} does not exist")
        return {"moved": 0}
    if not vault.is_mounted():
        raise vault.VaultUnavailable(
            f"vault not mounted at {config.VAULT_MOUNT} — run `mvault vault open` first"
        )

    moved = 0
    for item in sorted(qdir.iterdir()):
        if not item.is_file():
            continue
        dst = config.VAULT_MOUNT / item.name
        if dst.exists():
            dst = config.VAULT_MOUNT / f"migrated-{moved:04d}-{item.name}"
        shutil.copy2(item, dst)
        with open(dst, "rb") as f:
            os.fsync(f.fileno())
        _shred(item)
        moved += 1
        print(f"  vaulted + shredded: {item.name}")

    remaining = [p for p in qdir.iterdir() if p.is_file()]
    if not remaining:
        qdir.rmdir()
        print(f"migrated {moved} files; {qdir} removed")
    else:
        print(f"WARNING: {len(remaining)} items could not be migrated — folder kept")
    return {"moved": moved, "remaining": len(remaining)}
