#!/usr/bin/env bash
# Memory Vault — nightly ongoing pipeline (runs on the VM via cron).
# New photos flow: discover -> ingest -> curate -> screen* -> tag -> geocode
# -> faces -> graph/notes. (*screen politely halts if the vault is locked —
# new photos then wait as 'staged' until the next human vault-open; that
# friction is the privacy design, not a bug.)
set -uo pipefail
export MEMORYVAULT_LIBRARY_ROOT="${MEMORYVAULT_LIBRARY_ROOT:-$HOME/Constellation/library}"
export MEMORYVAULT_NSFW_MODEL_PATH=/opt/memoryvault/nsfw-model
export MEMORYVAULT_ROOT="$HOME/vault-view"
MV="/opt/memoryvault/venv/bin/python $HOME/constellation/scripts/mvault"

echo "=== nightly $(date -Is) ==="
# sources: one per line in $SOURCES_FILE (household-specific, not in git)
SOURCES_FILE="${MEMORYVAULT_SOURCES_FILE:-$HOME/.config/constellation/sources}"
if [ -f "$SOURCES_FILE" ]; then
  while IFS= read -r src; do [ -n "$src" ] && $MV discover "$src" --kind local; done < "$SOURCES_FILE"
fi
$MV ingest
$MV curate
$MV bursts || true     # keep the sharpest of each burst, park the rest
$MV screen || echo "screen skipped (vault locked) — staged photos wait"
$MV tag || true
$MV geocode
$MV faces scan || true
$MV faces cluster || true
$MV edges && $MV notes
$MV placards || true   # witty wall labels from tags/captions (text-only, cheap)
$MV status
