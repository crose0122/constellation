# Memory Vault — Deployment Examples

These scripts are generic starting points for a self-hosted installation. They do not describe a live deployment. Keep machine names, addresses, mount points, storage labels, credentials, inventory, and service evidence in local configuration outside this repository.

## 1. Choose a layout

A single Linux machine can run the pipeline, database, local web app, and optional inference service. Larger installations may place inference on another LAN machine or add a read-only standby. Choose roles explicitly; never run two writers against one library.

Minimum example resources are 4 CPU cores, 8 GB RAM, a 32 GB operating-system disk, and storage sized for the photo library and derivatives. These are examples, not measurements from a specific installation.

## 2. Optional VM provisioning

`provision-vm.sh` is a generic Proxmox helper. Supply a unique VM ID, generic VM name, storage pool, and bridge for your environment:

```bash
VMID=401 VMNAME=constellation-node STORAGE=local-lvm BRIDGE=vmbr0 ./provision-vm.sh
```

Review the generated commands before formatting or mounting any disk.

## 3. Setup inside the target

```bash
git clone <this-repository-url> ~/constellation
cd ~/constellation/deploy
./setup-vm.sh
```

The setup script installs dependencies, fetches the pinned screening model, initializes the library, runs the test suite as a health check, and installs these example units:

| Unit | Purpose |
|---|---|
| `memoryvault-constellation.service` | local Constellation web service |
| `memoryvault-vault-autoclose.timer` | closes an idle LUKS vault |
| `memoryvault-db-snapshot.timer` | creates a consistent SQLite backup |

Ports and paths come from local configuration. Do not commit the resulting environment file.

## 4. Optional standby replication

Replicate immutable originals, generated thumbnails, duplicate bins, opaque encrypted vault blobs, and completed SQLite snapshots. Never file-sync a live SQLite database or its WAL/SHM files.

A standby reads the newest completed snapshot. Promotion is an operator action: stop the old writer, copy a verified snapshot into place, promote exactly one node, and run an integrity check before resuming the pipeline.

## 5. Vault ceremony

Create and open a vault only from an interactive local shell:

```bash
mvault vault create
mvault vault open
```

Enter the passphrase only at the cryptsetup prompt. Store recovery material offline or in a trusted password manager; never place it in a repository, chat, synced vault, or deployment log. The encrypted blob may be backed up without exposing its contents.

If migrating a legacy plaintext quarantine, verify the destination vault is open before running:

```bash
mvault migrate-quarantine
mvault vault close
```

## 6. First-run gate

Use synthetic calibration fixtures first, then configure each source read-only:

```bash
mvault discover /path/to/source --kind usb
mvault calibrate --safe /path/to/safe-fixtures --flagged /path/to/flagged-fixtures
export MEMORYVAULT_SCREEN_T_LOW=<approved-threshold>
mvault ingest --limit 500 --sample
mvault vault open
mvault screen && mvault tag && mvault edges && mvault notes
mvault status
```

Open the address printed by the configured service. Keep real hostnames, addresses, source paths, counts, and acceptance logs private.
