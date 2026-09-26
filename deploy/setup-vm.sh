#!/usr/bin/env bash
# Memory Vault — in-VM setup. Run INSIDE the freshly provisioned VM:
#
#   git clone <repo> ~/constellation && cd ~/constellation/deploy && ./setup-vm.sh
#
# Idempotent. Installs deps, initializes the library, downloads the pass-1
# classifier weights (one-time; offline afterwards), runs the test suite as a
# health check, and installs systemd units (Constellation server, vault auto-close,
# nightly DB snapshot).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$REPO_DIR/scripts"
LIBRARY_ROOT="${MEMORYVAULT_LIBRARY_ROOT:-$HOME/Constellation/library}"
NSFW_DIR="${MEMORYVAULT_NSFW_MODEL_PATH:-/opt/memoryvault/nsfw-model}"

echo "== packages =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3-pip python3-venv cryptsetup rsync sqlite3

echo "== python env =="
sudo mkdir -p /opt/memoryvault && sudo chown "$USER" /opt/memoryvault
python3 -m venv /opt/memoryvault/venv
/opt/memoryvault/venv/bin/pip install --quiet Pillow pillow-heif imagehash requests
# pass-1 classifier (CPU): torch+transformers — big but local-only afterwards
/opt/memoryvault/venv/bin/pip install --quiet torch torchvision --index-url https://download.pytorch.org/whl/cpu || \
  /opt/memoryvault/venv/bin/pip install --quiet torch torchvision
# torchvision is required by AutoImageProcessor for the NSFW model (hit live 2026-07-23)
/opt/memoryvault/venv/bin/pip install --quiet transformers

echo "== classifier weights (one-time download) =="
if [ ! -d "$NSFW_DIR" ]; then
  /opt/memoryvault/venv/bin/python - "$NSFW_DIR" <<'EOF'
import sys
from transformers import AutoModelForImageClassification, AutoImageProcessor
target = sys.argv[1]
m = AutoModelForImageClassification.from_pretrained("Falconsai/nsfw_image_detection")
p = AutoImageProcessor.from_pretrained("Falconsai/nsfw_image_detection")
m.save_pretrained(target); p.save_pretrained(target)
print(f"weights saved to {target} — no further network access needed")
EOF
fi

echo "== library init =="
export MEMORYVAULT_LIBRARY_ROOT="$LIBRARY_ROOT"
export MEMORYVAULT_NSFW_MODEL_PATH="$NSFW_DIR"
/opt/memoryvault/venv/bin/python "$SCRIPTS/mvault" init

echo "== health check: test suite =="
/opt/memoryvault/venv/bin/python "$SCRIPTS/tests/test_core.py"

echo "== systemd units =="
sudo cp "$REPO_DIR"/deploy/systemd/*.service "$REPO_DIR"/deploy/systemd/*.timer /etc/systemd/system/
sudo sed -i "s|@REPO@|$REPO_DIR|g; s|@USER@|$USER|g; s|@LIBRARY@|$LIBRARY_ROOT|g; s|@NSFW@|$NSFW_DIR|g" \
  /etc/systemd/system/memoryvault-*.service /etc/systemd/system/memoryvault-*.timer
sudo systemctl daemon-reload
sudo systemctl enable --now memoryvault-constellation.service
sudo systemctl enable --now memoryvault-vault-autoclose.timer
sudo systemctl enable --now memoryvault-db-snapshot.timer

echo
echo "Done. Next steps (in order):"
echo "  1. vault ceremony (both vault keyholders, in person):  mvault vault create"
echo "  2. migrate legacy quarantine (if this machine has one):"
echo "     mvault vault open && mvault migrate-quarantine && mvault vault close"
echo "  3. point discovery at sources:  mvault discover /mnt/<source> --kind usb"
echo "  4. calibrate screening:  mvault calibrate --safe <dir> --flagged <dir>"
echo "  5. sample run:  mvault ingest --limit 500 --sample && mvault vault open && \\"
echo "     mvault screen && mvault tag && mvault edges && mvault notes"
echo "Constellation: http://<this-vm>:8484/  (ambient: /ambient)"
