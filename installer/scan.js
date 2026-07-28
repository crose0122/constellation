// Constellation Setup — system analysis engine.
// Detects GPU (and VRAM), CPU/RAM, storage locations with free space, likely
// photo folders, and other computers on the LAN. Primary target is Windows
// (nvidia-smi + PowerShell/WMI), with POSIX fallbacks so it also runs on
// Mac/Linux. Everything is best-effort: a failed probe returns nulls, never
// throws, so the wizard can still proceed.
"use strict";
const os = require("os");
const path = require("path");
const fs = require("fs");
const { execFile } = require("child_process");

const IS_WIN = process.platform === "win32";
const IS_MAC = process.platform === "darwin";

function run(cmd, args, { timeout = 15000 } = {}) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout, windowsHide: true, maxBuffer: 8 << 20 },
      (err, stdout) => resolve(err ? "" : String(stdout)));
  });
}
// run a PowerShell one-liner (Windows only)
function ps(script) {
  return run("powershell.exe",
    ["-NoProfile", "-NonInteractive", "-Command", script]);
}

// ----------------------------------------------------------------- GPU
function vendorOf(name) {
  const n = (name || "").toLowerCase();
  if (/nvidia|geforce|\brtx\b|\bgtx\b|quadro|tesla/.test(n)) return "NVIDIA";
  if (/radeon|\bamd\b|\brx ?\d|vega|instinct|firepro/.test(n)) return "AMD";
  if (/intel|\barc\b|iris|\buhd\b|\bhd graphics\b/.test(n)) return "Intel";
  if (/apple/.test(n)) return "Apple";
  return "Unknown";
}
// integrated GPUs (iGPU/APU) can't usefully run the 7B — flag them so the
// recommendation routes to CPU rather than pretending they're accelerators
function isIntegrated(name) {
  const n = (name || "").toLowerCase();
  return /\buhd\b|\bhd graphics\b|iris|vega \d|radeon\(tm\) graphics|radeon graphics$/.test(n);
}
function accelFor(vendor, name, vramGB) {
  if (vendor === "NVIDIA") return "cuda";
  if (vendor === "Apple") return "metal";
  if (isIntegrated(name)) return "cpu";
  if (vendor === "AMD") return "rocm";           // Ollama runs AMD via ROCm/HIP
  if (vendor === "Intel") return /\barc\b/.test((name || "").toLowerCase()) ? "vulkan" : "cpu";
  return "cpu";
}

async function scanGPU() {
  const out = { vendor: null, name: null, vramGB: null, driver: null,
                accel: "cpu", detail: null, all: [] };
  // 1) nvidia-smi — authoritative for NVIDIA (confirms CUDA + exact VRAM)
  const smi = await run(IS_WIN ? "nvidia-smi.exe" : "nvidia-smi",
    ["--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"]);
  if (smi.trim()) {
    const [name, memMiB, driver] = smi.split("\n")[0].split(",").map((s) => s.trim());
    Object.assign(out, { vendor: "NVIDIA", name, driver, accel: "cuda",
      vramGB: Math.round((parseFloat(memMiB) / 1024) * 10) / 10 });
    out.all = [{ name, vendor: "NVIDIA", vramGB: out.vramGB, accel: "cuda" }];
    return out;
  }
  // 2) Windows: enumerate ALL adapters. Accurate VRAM comes from the driver
  //    registry key (qwMemorySize, uint64) — WMI AdapterRAM caps at 4GB.
  if (IS_WIN) {
    const reg = await ps(
      "Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\*' -EA SilentlyContinue " +
      "| ForEach-Object { [pscustomobject]@{ name=$_.DriverDesc; vram=$_.'HardwareInformation.qwMemorySize'; drv=$_.DriverVersion } } " +
      "| Where-Object { $_.name } | ConvertTo-Json -Compress");
    const gpus = [];
    try {
      let list = JSON.parse(reg || "[]"); if (!Array.isArray(list)) list = [list];
      for (const g of list) {
        const name = g.name;
        const vramGB = g.vram ? Math.round(Number(g.vram) / 1e9 * 10) / 10 : null;
        const vendor = vendorOf(name);
        gpus.push({ name, vendor, driver: g.drv || null, vramGB,
          accel: accelFor(vendor, name, vramGB), integrated: isIntegrated(name) });
      }
    } catch { /* fall through to WMI */ }
    if (!gpus.length) {  // last-ditch: WMI name only
      const j = await ps("Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion | ConvertTo-Json -Compress");
      try { let l = JSON.parse(j || "[]"); if (!Array.isArray(l)) l = [l];
        for (const g of l) { const vendor = vendorOf(g.Name);
          gpus.push({ name: g.Name, vendor, driver: g.DriverVersion || null, vramGB: null,
            accel: accelFor(vendor, g.Name, null), integrated: isIntegrated(g.Name) }); }
      } catch { /* */ }
    }
    out.all = gpus;
    // prefer a real accelerator with the most VRAM over an integrated chip
    const pick = gpus.slice().sort((a, b) =>
      (b.accel !== "cpu") - (a.accel !== "cpu") || (b.vramGB || 0) - (a.vramGB || 0))[0];
    if (pick) Object.assign(out, pick);
    if (out.vendor === "NVIDIA" && out.accel === "cuda")
      out.detail = "NVIDIA card found but nvidia-smi didn't respond — the driver may need installing/updating for GPU acceleration.";
    if (out.vendor === "AMD")
      out.detail = "AMD GPU — Constellation runs it through Ollama's ROCm/HIP support (works on recent Radeon cards; a current Adrenalin driver helps).";
    if (out.vendor === "Intel" && out.accel === "vulkan")
      out.detail = "Intel Arc — GPU acceleration is experimental in Ollama; the wizard defaults to CPU but you can try the GPU.";
    return out;
  }
  // 3) Apple Silicon
  if (IS_MAC) {
    const sp = await run("system_profiler", ["SPDisplaysDataType"]);
    const m = sp.match(/Chipset Model:\s*(.+)/);
    Object.assign(out, { vendor: "Apple", name: m ? m[1].trim() : "Apple GPU",
      accel: "metal", vramGB: Math.round(os.totalmem() / 1e9 * 0.6) });
    return out;
  }
  // 4) Linux: rocm-smi (AMD) then lspci
  const rocm = await run("bash", ["-lc", "rocm-smi --showproductname --showmeminfo vram 2>/dev/null"]);
  if (rocm.trim()) { out.vendor = "AMD"; out.accel = "rocm"; out.name = "AMD Radeon (ROCm)"; }
  const lspci = await run("bash", ["-lc", "lspci | grep -iE 'vga|3d'"]);
  if (lspci.trim() && !out.name) {
    out.name = lspci.split("\n")[0].split(":").slice(2).join(":").trim();
    out.vendor = vendorOf(out.name); out.accel = accelFor(out.vendor, out.name, null);
  }
  return out;
}

// ----------------------------------------------------------------- CPU / RAM
function scanSystem() {
  const cpus = os.cpus() || [];
  return {
    platform: process.platform,
    cpu: cpus[0] ? cpus[0].model.trim() : "unknown",
    cores: cpus.length,
    ramGB: Math.round(os.totalmem() / 1e9),
    hostname: os.hostname(),
  };
}

// ----------------------------------------------------------------- Storage
const PHOTO_HINTS = ["pictures", "photos", "onedrive", "dcim", "camera", "icloud"];
async function scanStorage() {
  const drives = [];
  if (IS_WIN) {
    const j = await ps(
      "Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID,DriveType,FreeSpace,Size,VolumeName | ConvertTo-Json -Compress");
    try {
      let list = JSON.parse(j || "[]"); if (!Array.isArray(list)) list = [list];
      for (const d of list) {
        if (!d.Size) continue;
        drives.push({
          path: d.DeviceID + "\\",
          label: d.VolumeName || "",
          totalGB: Math.round(d.Size / 1e9),
          freeGB: Math.round(d.FreeSpace / 1e9),
          removable: d.DriveType === 2,     // 2 = removable (USB)
          type: d.DriveType === 4 ? "network" : d.DriveType === 2 ? "removable" : "fixed",
        });
      }
    } catch { /* ignore */ }
  } else {
    const df = await run("bash", ["-lc", "df -kP | tail -n +2"]);
    for (const line of df.trim().split("\n")) {
      const p = line.split(/\s+/);
      if (p.length < 6) continue;
      const mount = p.slice(5).join(" ");
      const totalGB = Math.round(parseInt(p[1]) / 1e6);
      if (totalGB < 1) continue;   // skip tmpfs / pseudo mounts
      if (/^\/(dev|proc|sys|run|boot|snap)(\/|$)/.test(mount)) continue;
      drives.push({
        path: mount, label: "",
        totalGB,
        freeGB: Math.round(parseInt(p[3]) / 1e6),
        removable: /\/(media|mnt|Volumes)\//.test(mount), type: "fixed",
      });
    }
  }
  // likely photo folders under the user's home + drive roots
  const candidates = [];
  const home = os.homedir();
  for (const sub of ["Pictures", "OneDrive", "Photos", "Pictures/Camera Roll", "iCloudPhotos"]) {
    const p = path.join(home, sub);
    try { if (fs.existsSync(p)) candidates.push(p); } catch { /* */ }
  }
  for (const d of drives) {
    if (d.removable) candidates.push(d.path); // USB drives often hold photo archives
  }
  return { drives, photoCandidates: candidates };
}

// ----------------------------------------------------------------- Network
async function scanNetwork() {
  const machines = [];
  // seed from the ARP cache (fast, no sweep); names resolved best-effort
  const arp = await run(IS_WIN ? "arp" : "arp", ["-a"]);
  const selfIps = new Set(Object.values(os.networkInterfaces()).flat()
    .filter((i) => i && i.family === "IPv4" && !i.internal).map((i) => i.address));
  const seen = new Set();
  const re = /(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:-]{11,17})/g;
  let m;
  while ((m = re.exec(arp))) {
    const ip = m[1], mac = m[2].replace(/-/g, ":").toLowerCase();
    if (seen.has(ip) || selfIps.has(ip) || ip.endsWith(".255") ||
        mac === "ff:ff:ff:ff:ff:ff" || ip.startsWith("224.") || ip.startsWith("239.")) continue;
    seen.add(ip);
    machines.push({ ip, mac, hostname: null });
  }
  return { machines, self: [...selfIps] };
}

// --------------------------------------------------- Model recommendation
// The vision model is qwen2.5vl:7b (~5.5GB weights, wants ~7-8GB VRAM). The 3b
// variant is broken on modern Ollama, so we NEVER recommend it — under-spec
// cards get 7b with CPU offload (works, just slower) rather than a bad model.
function recommendModel(gpu, sys) {
  const v = gpu.vramGB || 0;
  const base = { model: "qwen2.5vl:7b", downloadGB: 6 };
  // modes = what the user is allowed to switch to in the wizard (override).
  const gpuCpu = ["gpu", "cpu"], cpuOnly = ["cpu"];
  const label = gpu.name || "your system";

  // NVIDIA (CUDA) — the best-supported path
  if (gpu.accel === "cuda") {
    if (v >= 8) return { ...base, mode: "gpu", speed: "fast", ok: true, modes: gpuCpu,
      note: `${label} has ${v} GB VRAM — plenty for full-GPU tagging.` };
    if (v >= 6) return { ...base, mode: "gpu", speed: "good", ok: true, modes: gpuCpu,
      note: `${label} (${v} GB) fits the 7B model with a little headroom.` };
    return { ...base, mode: "gpu-offload", speed: "moderate", ok: true, modes: gpuCpu,
      note: `${label} has only ${v || "<6"} GB VRAM — the 7B model spills onto the CPU (works, just slower). An 8 GB+ card would be much faster.` };
  }
  // Apple Silicon (Metal)
  if (gpu.accel === "metal")
    return { ...base, mode: "metal", speed: sys.ramGB >= 16 ? "good" : "moderate", ok: true, modes: gpuCpu,
      note: `Apple GPU with ${sys.ramGB} GB unified memory — runs the 7B model on Metal.` };
  // AMD (ROCm/HIP via Ollama)
  if (gpu.accel === "rocm") {
    if (v >= 8) return { ...base, mode: "gpu", speed: "good", ok: true, modes: gpuCpu,
      note: `${label} (${v} GB) — runs the 7B model on the GPU via ROCm. AMD support is newer than NVIDIA's; keep the Adrenalin driver current.` };
    if (v >= 6 || v === 0) return { ...base, mode: "gpu-offload", speed: "moderate", ok: true, modes: gpuCpu,
      note: `${label}${v ? " (" + v + " GB)" : ""} — AMD via ROCm with some CPU offload. If the GPU path misbehaves, switch to CPU.` };
    return { ...base, mode: "cpu", speed: "slow", ok: sys.ramGB >= 12, modes: gpuCpu,
      note: `${label} has little VRAM — recommend CPU tagging (slow). You can try the GPU if you like.` };
  }
  // Intel Arc — experimental GPU accel; default to CPU but allow trying it
  if (gpu.accel === "vulkan")
    return { ...base, mode: "cpu", speed: "slow", ok: sys.ramGB >= 12, modes: gpuCpu,
      note: `${label} — Intel Arc GPU acceleration is experimental in Ollama, so this defaults to CPU (slow but reliable). You can try the GPU from the dropdown.` };
  // Integrated GPU or nothing usable → CPU
  return { ...base, mode: "cpu", speed: "slow", ok: sys.ramGB >= 12, modes: cpuOnly,
    note: gpu.integrated
      ? `${label} is an integrated GPU — not enough for the 7B model, so tagging runs on the CPU (correct but slow; leave it overnight). ${sys.ramGB} GB RAM.`
      : gpu.name
      ? `No usable GPU acceleration on ${label} — CPU tagging (slow). ${sys.ramGB} GB RAM.`
      : `No GPU detected — CPU-only tagging (slow). ${sys.ramGB} GB RAM.` };
}

async function fullScan() {
  const [gpu, storage, network] = await Promise.all([scanGPU(), scanStorage(), scanNetwork()]);
  const sys = scanSystem();
  return { sys, gpu, storage, network, recommendation: recommendModel(gpu, sys) };
}

module.exports = { fullScan, scanGPU, scanSystem, scanStorage, scanNetwork, recommendModel };
