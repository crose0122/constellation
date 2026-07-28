#!/usr/bin/env bash
# Build the native Constellation backend (memoryvault-brain) on Linux/macOS.
# Output: dist/memoryvault-brain/  (an onedir bundle the Electron installer ships).
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .buildvenv
./.buildvenv/bin/pip install --upgrade pip pyinstaller -r requirements-backend.txt
./.buildvenv/bin/pyinstaller --noconfirm --clean constellation-backend.spec
echo "Built: $(pwd)/dist/memoryvault-brain/"
