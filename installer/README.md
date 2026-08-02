# Constellation Setup — GUI installer

A native desktop setup wizard (Electron) that gets a non-technical person from
zero to a running Constellation in a few clicks:

1. **Welcome**
2. **System scan** — detects the graphics card + **VRAM**, CPU, RAM, drives with
   free space, likely photo folders, and other computers on the LAN. From the
   GPU it **recommends a vision model** (see below).
3. **Storage** — pick the library location and which folders to pull photos &
   videos from (read-only).
4. **Download AI** — installs Ollama and pulls the vision model **onto the GPU**,
   with live progress.
5. **First sweep** — reads the chosen folders and builds the library, so the app
   opens with photos in it (see below).
6. **Finish** — launches the stack, starts the model-bound stages in the
   background, opens the app, and gives you the LAN address for a TV or tablet.

## The first sweep (why the wizard has to run the pipeline)

Installing the software was never the job — *seeing your photos* is. The wizard
used to write a config, start the server and open a star map with nothing in
it; the folders picked on the storage step were written to the `.env` as
`MEMORYVAULT_SOURCES` and then read by nothing at all.

The sweep is split because the stages differ wildly in cost:

| | stages | cost |
|---|---|---|
| **foreground** (step 5) | `init` → `discover` → `ingest` → `curate` | hashing and EXIF only — minutes |
| **background** (after step 6) | `screen` → `tag` → `geocode` → `describe` → `faces` → `edges` | every model-bound stage — hours on CPU |

The foreground set runs to completion with live progress, so the app genuinely
has content when it opens. The background set is spawned **detached**, so it
survives the wizard closing, and each stage is allowed to fail without killing
the ones after it — no Ollama means no tags, but faces and the memory graph
still get built. The app's own `/progress` page reports it filling in.

Stage order matches `docker/entrypoint.sh`, which is the canonical chain. Only
`init` is fatal if it fails: without a database nothing downstream can run.

## The model recommendation (why it never picks the 3B)

The vision model is **`qwen2.5vl:7b`** (~6 GB, wants ~7–8 GB VRAM). The 3B
variant is broken on modern Ollama (degenerate output), so the scanner will
**never** silently fall back to it. Instead:

| Detected GPU | Path | Recommendation |
|---|---|---|
| NVIDIA ≥ 8 GB VRAM | CUDA | 7B, full GPU — *fast* |
| NVIDIA 6–8 GB | CUDA | 7B, GPU — *good* |
| NVIDIA < 6 GB | CUDA | 7B with CPU offload — *works, slower*; suggests an 8 GB+ card |
| AMD Radeon ≥ 8 GB | ROCm/HIP | 7B on GPU — *good*; notes AMD support is newer, keep drivers current |
| AMD Radeon 6–8 GB / unknown | ROCm | 7B with some CPU offload; can switch to CPU if it misbehaves |
| Apple Silicon | Metal | 7B on Metal |
| Intel Arc | Vulkan (experimental) | Defaults to **CPU** (reliable); GPU offered as an option |
| Integrated GPU (Intel UHD/Iris, AMD APU) | — | 7B on CPU — *slow* |
| No GPU | — | 7B on CPU — *slow*, warns to run overnight |

Detection (`scan.js`) reads accurate VRAM from the Windows driver registry
(`qwMemorySize`, uint64 — WMI's `AdapterRAM` caps at 4 GB), picks the best
accelerator over any integrated chip, and the recommendation logic lives in
`recommendModel()`. Whenever more than one path is viable, the wizard shows a
**"Run the AI on: graphics card / CPU"** dropdown so the user can override.

That logic lives in `scan.js → recommendModel()`.

## Run in dev

```bash
cd installer
npm install
npm start          # opens the wizard against your real hardware
```

The scanner (`scan.js`) is cross-platform (Windows via `nvidia-smi` +
PowerShell/WMI; macOS via `system_profiler`; Linux via `nvidia-smi`/`lspci`),
so `npm start` shows real detection on any OS.

## Build the Windows installer

```bash
cd installer
npm install
npm run dist       # -> dist/Constellation Setup Setup <ver>.exe (NSIS)
```

`npm run dist:mac` / `npm run dist:linux` produce a `.dmg` / `.AppImage`.

## Native backend bundle (no Docker, no Python for the user)

The wizard's final step launches a **self-contained backend executable** —
`memoryvault-brain(.exe)` — built with PyInstaller from `../scripts`. It's the
whole Constellation engine (web UI + every pipeline stage) in one folder, so
the target machine needs neither Python nor Docker.

Build it (produces `../scripts/dist/memoryvault-brain/`, which `electron-builder`
ships into the app as `resources/backend/`):

```bash
# on the target OS (PyInstaller does not cross-compile):
cd ../scripts
./build-backend.sh          # Linux/macOS
# or, on Windows:
powershell -File build-backend.ps1
```

Then build the installer (`npm run dist`) and `launchStack()` runs the bundle
directly; if the bundle is absent it falls back to `docker compose`.

**Validated:** the bundle was built and run on Linux — `init`, `status`, and the
`brain` web server all work from the native binary (imports, static assets, and
the tag schema are correctly packaged). The `.spec` collects the heavy ML libs
(insightface, onnxruntime, opencv) and the model weights download at first use,
same as Ollama's.

## Remaining before handing it to a non-technical user

- **Icons** — drop `assets/icon.ico` / `icon.icns`.
- **Code signing** — an unsigned `.exe` triggers SmartScreen; sign it.
- **Test on real Windows hardware** — GPU detection is written against the
  documented Windows commands and validated on Linux's fallback paths; run the
  `.exe` on an actual Windows box with an NVIDIA/AMD card before shipping.
  The sweep's Windows chain (`cmd /c "… & … & …"`) is likewise validated by
  construction and on the POSIX path, not yet on Windows itself.
- **Ship the Android APK** — the TV app currently has to be built from source
  with a JDK and the Android SDK. A prebuilt, debug-signed `.apk` next to the
  desktop installer would make the TV step as easy as the rest.
