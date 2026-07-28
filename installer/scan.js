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
async function scanGPU() {
  const out = { vendor: null, name: null, vramGB: null, driver: null,
                accel: "cpu", detail: null };
  // 1) nvidia-smi is the gold source when an NVIDIA card + driver are present
  const smi = await run(IS_WIN ? "nvidia-smi.exe" : "nvidia-smi",
    ["--query-gpu=name,memory.total,driver_version",
     "--format=csv,noheader,nounits"]);
  if (smi.trim()) {
    const [name, memMiB, driver] = smi.split("\n")[0].split(",").map((s) => s.trim());
    out.vendor = "NVIDIA"; out.name = name; out.driver = driver;
    out.vramGB = Math.round((parseFloat(memMiB) / 1024) * 10) / 10;
    out.accel = "cuda";
    return out;
  }
  // 2) Windows without a working nvidia driver: WMI still reports the adapter
  if (IS_WIN) {
    const j = await ps(
      "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json -Compress");
    try {
      let list = JSON.parse(j || "null");
      if (list && !Array.isArray(list)) list = [list];
      // prefer a discrete card (most VRAM) over integrated
      const best = (list || []).sort(
        (a, b) => (b.AdapterRAM || 0) - (a.AdapterRAM || 0))[0];
      if (best) {
        out.name = best.Name;
        out.driver = best.DriverVersion || null;
        // AdapterRAM is a uint32 and caps at ~4GB — treat as a floor
        if (best.AdapterRAM) out.vramGB = Math.round(best.AdapterRAM / 1e9 * 10) / 10;
        const n = (best.Name || "").toLowerCase();
        out.vendor = n.includes("nvidia") || n.includes("geforce") || n.includes("rtx") ? "NVIDIA"
          : n.includes("amd") || n.includes("radeon") ? "AMD"
          : n.includes("intel") ? "Intel" : "Unknown";
        if (out.vendor === "NVIDIA")
          out.detail = "NVIDIA card detected but the driver isn't responding — a driver install/update may be needed for GPU acceleration.";
      }
    } catch { /* leave nulls */ }
    return out;
  }
  // 3) Apple Silicon: the unified memory is the GPU budget
  if (IS_MAC) {
    const sp = await run("system_profiler", ["SPDisplaysDataType"]);
    const m = sp.match(/Chipset Model:\s*(.+)/);
    out.vendor = "Apple"; out.name = m ? m[1].trim() : "Apple GPU";
    out.accel = "metal";
    out.vramGB = Math.round(os.totalmem() / 1e9 * 0.6); // usable unified budget
    return out;
  }
  // 4) Linux fallback: lspci
  const lspci = await run("bash", ["-lc", "lspci | grep -i vga"]);
  if (lspci.trim()) out.name = lspci.split("\n")[0].split(":").slice(2).join(":").trim();
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
  if (gpu.accel === "cuda" && v >= 8)
    return { ...base, mode: "gpu", speed: "fast", ok: true,
      note: `Your ${gpu.name} has ${v} GB VRAM — plenty for full-GPU tagging.` };
  if (gpu.accel === "cuda" && v >= 6)
    return { ...base, mode: "gpu", speed: "good", ok: true,
      note: `Your ${gpu.name} (${v} GB) fits the 7B model with a little headroom to spare.` };
  if (gpu.accel === "metal")
    return { ...base, mode: "metal", speed: sys.ramGB >= 16 ? "good" : "moderate", ok: true,
      note: `Apple GPU with ${sys.ramGB} GB unified memory — runs the 7B model on Metal.` };
  if (gpu.accel === "cuda" && v > 0)
    return { ...base, mode: "gpu-offload", speed: "moderate", ok: true,
      note: `Your ${gpu.name} has only ${v} GB VRAM — the 7B model runs partly on the CPU (slower, but works). A card with 8 GB+ would be much faster.` };
  return { ...base, mode: "cpu", speed: "slow", ok: sys.ramGB >= 12,
    note: gpu.name
      ? `No usable GPU acceleration detected on your ${gpu.name}. Tagging will run on the CPU — correct but slow (leave it running overnight). ${sys.ramGB} GB RAM.`
      : `No GPU detected — CPU-only tagging (slow). ${sys.ramGB} GB RAM.` };
}

async function fullScan() {
  const [gpu, storage, network] = await Promise.all([scanGPU(), scanStorage(), scanNetwork()]);
  const sys = scanSystem();
  return { sys, gpu, storage, network, recommendation: recommendModel(gpu, sys) };
}

module.exports = { fullScan, scanGPU, scanSystem, scanStorage, scanNetwork, recommendModel };
