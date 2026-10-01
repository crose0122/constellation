#!/usr/bin/env bash
# Download + verify the screening model before packaging (V2 CP2).
set -euo pipefail
cd "$(dirname "$0")/models"
URL="https://huggingface.co/onnx-community/nsfw_image_detection-ONNX/resolve/main/onnx/model_quantized.onnx"
[ -f nsfw-screen.onnx ] || curl -fsSL -o nsfw-screen.onnx.part "$URL" && { [ -f nsfw-screen.onnx.part ] && mv nsfw-screen.onnx.part nsfw-screen.onnx || true; }
sha256sum -c nsfw-screen.onnx.sha256
