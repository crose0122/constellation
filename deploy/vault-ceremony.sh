#!/usr/bin/env bash
# Memory Vault — the vault ceremony (deploy/README.md §5).
#
# Run this FROM ANY MACHINE on the LAN, with both vault keyholders together at
# the keyboard:
#
#     ./vault-ceremony.sh                 # targets user@memory-vault-host
#     VM=user@other-host ./vault-ceremony.sh
#
# It copies itself to the VM and re-runs there over an interactive SSH
# session, so cryptsetup's passphrase prompts land on your real terminal.
# The passphrase is typed only at those prompts — never an argument, never
# in shell history, never on disk.
#
# What it does, in order:
#   1. Safety checks (no pipeline running, vault not mounted).
#   2. Replaces an existing vault image only after explicit DESTROY confirmation.
#   3. `mvault vault create` — cryptsetup prompts you both for the real
#      passphrase. Long phrase, not a PIN. Decide it together, out loud,
#      before running this.
#   4. Verifies the vault opens and mounts, then closes it.
#   5. Migrates a legacy plaintext Quarantine/ if one exists.
#
# Afterwards: write the passphrase on PAPER and store it offline. Not in a
# synced vault, not in a chat, not in a repository. If it's lost, vaulted
# content is gone — that's the point of the vault.
set -euo pipefail

VM="${VM:-user@memory-vault-host}"
VAULT_GB="${VAULT_GB:-50}"

if [[ "${1:-}" != "--remote" ]]; then
  echo "== Memory Vault ceremony =="
  echo "Target: $VM   (override with VM=user@host)"
  echo "Both keyholders should be present. Continue? [y/N]"
  read -r yn
  [[ "$yn" == "y" || "$yn" == "Y" ]] || { echo "aborted."; exit 1; }
  scp -q "$0" "$VM:/tmp/vault-ceremony.sh"
  exec ssh -t "$VM" "VAULT_GB=$VAULT_GB bash /tmp/vault-ceremony.sh --remote"
fi

# ---------- everything below runs ON the VM, interactively ----------
export MEMORYVAULT_LIBRARY_ROOT="${MEMORYVAULT_LIBRARY_ROOT:-$HOME/Constellation/library}"
MV="/opt/memoryvault/venv/bin/python $HOME/constellation/scripts/mvault"
IMG="$MEMORYVAULT_LIBRARY_ROOT/vault.img"

echo
echo "== 1/5 safety checks =="
if pgrep -af "mvault (ingest|screen|tag|retry)" >/dev/null; then
  echo "A pipeline stage is running — finish or stop it first."; exit 1
fi
if mountpoint -q /mnt/vault; then
  echo "Vault is mounted; closing it first..."
  sudo umount /mnt/vault
fi
sudo cryptsetup close memoryvault 2>/dev/null || true
echo "ok."

echo
echo "== 2/5 check for an existing vault =="
if [[ -f "$IMG" ]]; then
  echo "Found $IMG. Verify independently that it is safe to replace."
  echo "Type DESTROY to delete it and continue with the ceremony:"
  read -r confirm
  [[ "$confirm" == "DESTROY" ]] || { echo "aborted — nothing touched."; exit 1; }
  rm -f "$IMG"
  echo "temporary vault removed."
else
  echo "no existing vault image — clean slate."
fi

echo
echo "== 3/5 create the vault (${VAULT_GB} GB) =="
echo "cryptsetup will now ask for the passphrase — type it together."
$MV vault create --size "$VAULT_GB"

echo
echo "== 4/5 verify it opens =="
echo "Enter the passphrase once more to prove it works:"
$MV vault open
mountpoint -q /mnt/vault && echo "vault mounts: OK"
sudo chown "$USER:" /mnt/vault   # fresh ext4 root belongs to root otherwise
mkdir -p /mnt/vault/review
$MV vault close
echo "vault closed again (it only mounts on demand)."

echo
echo "== 5/5 legacy quarantine =="
QDIR="${MEMORYVAULT_ROOT:-~/memory-vault}/Quarantine"
if [[ -d "$QDIR" ]]; then
  echo "Legacy plaintext Quarantine found at $QDIR — migrating (vault will open)..."
  $MV vault open && $MV migrate-quarantine && $MV vault close
else
  echo "no legacy Quarantine/ — nothing to migrate (expected)."
fi

rm -f /tmp/vault-ceremony.sh
echo
echo "== ceremony complete =="
echo "1. Write the passphrase on PAPER, store it offline, tell no device."
echo "2. The full-library screening run can now go ahead — it will halt"
echo "   politely any time the vault isn't mounted when it needs it."
