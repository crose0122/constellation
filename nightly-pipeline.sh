#!/usr/bin/env bash
# Memory Vault — nightly ongoing pipeline (runs on the VM via cron).
# New photos flow: discover -> ingest -> curate -> screen* -> tag -> geocode
# -> faces -> graph/notes. (*screen politely halts if the vault is locked —
# new photos then wait as 'staged' until the next human vault-open; that
# friction is the privacy design, not a bug.)
set -uo pipefail
export MEMORYVAULT_LIBRARY_ROOT=~/Constellation/library
export MEMORYVAULT_NSFW_MODEL_PATH=/opt/memoryvault/nsfw-model
export MEMORYVAULT_ROOT="$HOME/vault-view"
MV="/opt/memoryvault/venv/bin/python $HOME/constellation/MemoryVault/.scripts/mvault"

echo "=== nightly $(date -Is) ==="
$MV discover /srv/photo-sources/Photos --kind local
$MV ingest
$MV curate
$MV screen || echo "screen skipped (vault locked) — staged photos wait"
$MV tag || true
$MV geocode
$MV faces scan || true
$MV faces cluster || true
$MV edges && $MV notes
$MV status
