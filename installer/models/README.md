# nsfw-screen.onnx

Private-photo screening model shipped with the installer, so a new install can
route adult photos to the vault with no GPU and no download.

- Source: `onnx-community/nsfw_image_detection-ONNX`, file `onnx/model_quantized.onnx`
  (int8 ONNX export of `Falconsai/nsfw_image_detection`, Apache-2.0).
- sha256: see `nsfw-screen.onnx.sha256`; must match the Hugging Face LFS hash.
- Parity with the original transformers model (V2 CP2 evidence): 52 images,
  max |score difference| 0.0035, same safe/review/vault verdict 52/52.

Not committed to git (87 MB). `fetch-models.sh` downloads and verifies it
before `npm run dist`.
