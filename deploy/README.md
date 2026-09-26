# Memory Vault — Deployment

Turnkey setup for the 2-VM Proxmox topology (SPEC.md §10). Inference stays
on the GPU host (Ollama); the VMs hold storage, the
pipeline, the DB, and Constellation (the web UI, formerly "The Brain").

## 1. Provision (run on each Proxmox host)

```bash
# host 1
./provision-vm.sh                          # a VM, the photo server VM
# host 2
VMID=402 VMNAME=photo-vm-2 ./provision-vm.sh
```

Defaults: 4 cores, 8 GB RAM, 32 GB OS disk, 500 GB data disk, DHCP.
Override `STORAGE`/`BRIDGE` if your Proxmox storage/bridge names differ.
Then inside each VM, format and mount the data disk (the script prints the
commands) — the library lives at `$HOME/Constellation/library`.

## 2. Setup (run inside each VM)

```bash
git clone <this repo> ~/constellation
cd ~/constellation/deploy && ./setup-vm.sh
```

This installs dependencies, downloads the pass-1 NSFW classifier weights
once (fully offline afterwards), initializes the library, **runs the test
suite as a health check**, and installs three systemd units:

| Unit | Purpose |
|---|---|
| `memoryvault-constellation.service` | Constellation on port 8484 |
| `memoryvault-vault-autoclose.timer` | unmounts the LUKS vault after 30 min idle |
| `memoryvault-db-snapshot.timer` | nightly `sqlite3 .backup` snapshot into `snapshots/` |

## 3. Roles: primary and standby

**the photo server VM is the writer** — pipeline runs here. **photo-vm-2 is the warm
standby** — it receives replicas and serves a read-only Constellation. Do not run
ingest/screen/tag on both.

## 4. Replication (primary → standby)

Replicate these paths (Syncthing folder or the rsync unit of your choice):

```
$HOME/Constellation/library/originals/      → sync
$HOME/Constellation/library/thumbnails/     → sync
$HOME/Constellation/library/duplicates/     → sync
$HOME/Constellation/library/vault.img       → sync (opaque encrypted blob)
$HOME/Constellation/library/snapshots/      → sync (consistent DB copies)
$HOME/Constellation/library/photos.db*      → NEVER sync the live file
```

The live `photos.db` (+ `-wal`/`-shm`) must never be file-synced — a
WAL-mode SQLite database replicated mid-write corrupts silently. The
standby's Constellation reads the latest file in `snapshots/` instead (point it at
it with `MEMORYVAULT_DB_PATH`).

**Failover:** if the primary dies, the standby has originals + last-night's
DB. Promote it by copying the newest snapshot to `photos.db` and running the
pipeline there; demote the old primary to standby when it returns.

## 5. Vault ceremony (humans only)

On the primary, both vault keyholders together:

```bash
mvault vault create        # cryptsetup prompts for the passphrase
```

Type the passphrase only at that prompt. Long phrase, not a PIN. Paper
backup stored offline — never in a synced vault, never in a chat, never in
`Home/API Keys.md`. The `vault.img` blob replicates to the standby
automatically and stays unreadable there without the passphrase.

If a legacy plaintext `Quarantine/` folder exists (from the pre-spec
pipeline), immediately after the ceremony run:

```bash
mvault vault open && mvault migrate-quarantine && mvault vault close
```

## 6. First real run (the M4 gates, in order)

```bash
mvault discover /mnt/<source> --kind usb        # per source, read-only
mvault calibrate --safe <dir> --flagged <dir>   # approve the threshold table
export MEMORYVAULT_SCREEN_T_LOW=<approved>
mvault ingest --limit 500 --sample              # the M1.5 sample
mvault vault open
mvault screen && mvault tag && mvault edges && mvault notes
mvault status                                   # errors_open must be 0 (or waived)
```

Then open Constellation at `http://the photo server VM:8484/` and enjoy.
