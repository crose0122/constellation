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
5. **Finish** — writes config, launches the stack, opens the app.

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

## What still needs doing before shipping to a non-technical user

This is a working wizard with real hardware detection and the Ollama +
model-download flow implemented. To make it a turnkey `.exe`, these remain:

- **Backend packaging.** The wizard's final step launches the Constellation
  backend. Right now `setup.js → launchStack()` looks for a bundled backend
  executable (`backend/memoryvault-brain.exe`) and falls back to `docker
  compose`. Ship one of:
  - a **PyInstaller** build of the `scripts/` Python backend (native, no Docker), or
  - **Docker Desktop** as a prerequisite (heavier for a non-technical user).
- **Icons** — drop `assets/icon.ico` / `icon.icns`.
- **Code signing** — an unsigned `.exe` triggers SmartScreen; sign it for a
  clean install.
- **Test on real Windows hardware** — the detection was validated on Linux
  (fallback paths) and is written against the documented Windows commands, but
  the `.exe` build + WMI/`nvidia-smi` paths should be run on an actual Windows
  box with an NVIDIA card before handing it to someone.

None of these change the wizard UX — they're packaging/hardening steps.
