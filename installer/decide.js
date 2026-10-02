// Constellation Setup — pure decisions, no I/O (V2 CP2).
// Everything the wizard *decides* lives here so it can be unit-tested with
// plain `node --test`, no Electron. scan.js / setup.js / main.js do the I/O.
"use strict";

// Spec A3: any spare PC with >=8 GB RAM and ~200 GB free. No GPU required.
const MIN_RAM_GB = 8;
const MIN_FREE_GB = 200;

// Plain-words hardware verdict for the "System scan" step.
function hardwareFloor(sys, drives, libraryRoot) {
  const problems = [];
  const warnings = [];
  const ram = Number(sys && sys.ramGB) || 0;
  if (ram < MIN_RAM_GB) {
    problems.push(`This computer has ${ram} GB of memory. Constellation needs at least ${MIN_RAM_GB} GB.`);
  }
  const target = pickDriveFor(libraryRoot, drives || []);
  const free = target ? Number(target.freeGB) || 0 : 0;
  if (!target) {
    warnings.push("I couldn't tell how much space is free where the library goes.");
  } else if (free < MIN_FREE_GB) {
    problems.push(`The library drive has ${free} GB free. Constellation needs about ${MIN_FREE_GB} GB for a family library.`);
  }
  return {
    ok: problems.length === 0,
    problems,
    warnings,
    freeGB: target ? free : null,
    hardwareGuidance: problems.length
      ? `Use a computer with at least ${MIN_RAM_GB} GB of memory and a drive with about ${MIN_FREE_GB} GB free. This installer does not provide a hardware shopping link.`
      : null,
  };
}

// --- where Constellation keeps its copy -------------------------------------
// Constellation's library is its own folder. Never the home folder itself
// (2026-10-02: a run picked ~ and scattered originals/, thumbnails/, .tls and
// photos.db straight into it), never a whole drive, never overlapping a photo
// folder it reads from, never on the backup drive. Plain string logic so the
// wizard page and the main process share it (no Node path module here).
function normPath(p) {
  if (typeof p !== "string" || !p.trim()) return "";
  let s = p.trim().replace(/\\/g, "/").replace(/\/+/g, "/");
  // resolve "." and ".." so "/tmp/.." and "/." are the root they really are
  if (s.startsWith("/") || /^[A-Za-z]:\//.test(s)) {
    const drive = /^[A-Za-z]:/.test(s) ? s.slice(0, 2) : "";
    const out = [];
    for (const seg of s.slice(drive.length).split("/")) {
      if (!seg || seg === ".") continue;
      if (seg === "..") out.pop(); else out.push(seg);
    }
    s = drive + "/" + out.join("/");
  }
  if (s.length > 1 && !/^[A-Za-z]:\/$/.test(s)) s = s.replace(/\/$/, "");
  return /^[A-Za-z]:/.test(s) ? s.toLowerCase() : s;   // Windows paths: case-insensitive
}
function samePath(a, b) { return !!a && a === b; }
function within(child, parent) {
  if (!child || !parent) return false;
  if (child === parent) return true;
  const pre = parent.endsWith("/") ? parent : parent + "/";
  return child.startsWith(pre);
}
function validateLibraryRoot(root, { home, sources = [], backupTarget } = {}) {
  const r = normPath(root);
  if (!r) return "Pick a place for Constellation's copy of your photos.";
  if (r === "/" || /^[a-z]:\/?$/.test(r)) {
    return "That's a whole drive. Pick or create a folder on it, like Constellation.";
  }
  if (samePath(r, normPath(home))) {
    return "That's your whole home folder. Constellation needs a folder of its own inside it, like Home › Constellation.";
  }
  for (const src of sources || []) {
    const sp = normPath(src);
    if (sp && (within(r, sp) || within(sp, r))) {
      return "That overlaps a folder your photos come from. Keep Constellation's copy somewhere separate, so your originals are never touched.";
    }
  }
  const bp = normPath(backupTarget);
  if (bp && (within(r, bp) || within(bp, r))) {
    return "That's on your backup drive. Keep Constellation's copy on this computer and the backup on the drive.";
  }
  return null;
}

// "Start my sky" is only enabled when everything it needs is valid. The PIN
// is cleared once handed to the backend, so without pinValid a retry after a
// failed setup would send an empty one.
function startReady({ pinValid, backupChoice, bkErr, backupOther }) {
  return !!pinValid && !!backupChoice && !bkErr && (backupChoice !== "other" || !!backupOther);
}

const WALL_URL = "http://localhost:8484/wall";

// The wall on the port this install actually got (main.js picks a free pair;
// 8484 is only the first choice).
function wallUrl(httpPort = 8484) {
  if (!Number.isInteger(httpPort) || httpPort < 1024 || httpPort > 65535) return WALL_URL;
  return `http://localhost:${httpPort}/wall`;
}

// Kept as a narrow pure decision for regression tests. This is deliberately a
// byte-for-byte contract rather than URL parsing/canonicalization: alternate
// schemes, hosts, case, ports, paths, escapes and URL metadata are all denied.
// The only accepted URL is the wall on this install's own port.
function isTrustedLocalUrl(raw, httpPort = 8484) {
  return typeof raw === "string" && raw === wallUrl(httpPort);
}

// Which source folders start ticked: the family's own photo folders, never a
// removable drive (that's usually the backup drive or someone's archive stick).
function defaultSourceChecked(candidate, drives) {
  const d = pickDriveFor(candidate, drives || []);
  return !(d && d.removable);
}

// Longest mount-point prefix wins (Linux "/", "/home", "/mnt/library"; Windows "C:\").
function pickDriveFor(p, drives) {
  if (!p) return null;
  const norm = (s) => String(s).replace(/\\/g, "/").toLowerCase().replace(/\/+$/, "") || "/";
  const target = norm(p);
  let best = null;
  for (const d of drives) {
    const m = norm(d.path);
    const hit = m === "/" ? target.startsWith("/") : (target === m || target.startsWith(m + "/"));
    if (hit && (!best || m.length > norm(best.path).length)) best = d;
  }
  return best;
}

// Spec A3: "no GPU needed" — CPU is the default. A real accelerator is an
// offer, never a requirement, and never the reason setup refuses to continue.
function recommendMode(gpu, sys) {
  const v = Number(gpu && gpu.vramGB) || 0;
  const accel = (gpu && gpu.accel) || "cpu";
  const fast = (accel === "cuda" && v >= 6) || (accel === "metal" && (sys.ramGB || 0) >= 16) ||
               (accel === "rocm" && v >= 8);
  return {
    mode: fast ? "gpu" : "cpu",
    modes: fast ? ["gpu", "cpu"] : ["cpu"],
    note: fast
      ? `${gpu.name} can speed up the first night of tagging. Constellation works fine without it.`
      : "Constellation will read your photos using this computer's processor. The first full pass runs overnight; new photos after that are quick.",
  };
}

// Storage math shown at the storage step (spec B5): plain words, round numbers.
function storageMath(freeGB) {
  const f = Number(freeGB) || 0;
  const photos = Math.floor((f * 1000) / 4);          // ~4 MB per phone photo
  const videoHours = Math.floor(f / 45);              // ~45 GB per hour of phone 4K
  return `${f} GB free holds about ${photos.toLocaleString("en-US")} photos, or ${videoHours} hours of phone video.`;
}

// Spec A6: PIN length and confirmation. Returns an error string or null.
function validatePin(pin, again) {
  const p = String(pin || "");
  if (p.length < 4) return "Use at least 4 digits.";
  if (p.length > 64) return "That's too long.";
  if (/^(\d)\1+$/.test(p)) return "Pick something less easy to guess than the same digit repeated.";
  if (["1234", "12345", "123456", "0000", "4321"].includes(p)) return "Pick something less easy to guess.";
  if (again !== undefined && p !== String(again)) return "The two PINs don't match.";
  return null;
}

// Spec A5: backup is mandatory. A backup target must be a *different* drive
// from the library, or it's not a backup.
function validateBackupTarget(target, libraryRoot, drives, sources) {
  if (!target) return "Pick a drive for backups. Your photos' only copy will live on this computer.";
  const norm = (s) => String(s).replace(/\\/g, "/").toLowerCase().replace(/\/+$/, "");
  const t = norm(target);
  for (const src of sources || []) {
    const s = norm(src);
    if (t === s || t.startsWith(s + "/") || s.startsWith(t + "/")) {
      return "The backup drive is also ticked as a photo source. Untick it above, or backups would be read back in as photos.";
    }
  }
  const a = pickDriveFor(target, drives || []);
  const b = pickDriveFor(libraryRoot, drives || []);
  if (a && b && a.path === b.path) return "That's the same drive as the library. A backup needs a different drive.";
  return null;
}

// The URLs shown on the finish screen.
function finishUrls(lan, httpPort = 8484, tlsPort = 8485) {
  const host = lan || "localhost";
  return {
    wall: `http://${host}:${httpPort}/wall`,
    app: `https://${host}:${tlsPort}/menu`,
    tv: `http://${host}:${httpPort}/?lite=1`,
    ca: `http://${host}:${httpPort}/ca.pem`,
  };
}

// The finish screen's truth table (task #130). Every claim the wizard makes
// about startup must be earned by a verified fact:
//   - startsItself  only when start-on-boot was verified — start-on-login is a
//     separate, weaker claim and never inferred from "the launch worked once";
//   - running       only when the exact Constellation readiness probe passes;
//   - ok=false      only when nothing is running — the family still deserves a
//     working wall tonight, so a one-off launch that works is a success that
//     honestly says it won't survive a reboot.
function finishClaims({ autostartOk, startsOnLogin, startsOnBoot, serverUp, background }) {
  // Process creation is not readiness.  Only the product-specific HTTP probe
  // may earn a claim that Constellation is running.
  const running = !!serverUp;
  const startsOnBootValue = !!(autostartOk && startsOnBoot);
  const startsOnLoginValue = !!(autostartOk && (startsOnLogin || startsOnBoot));
  const startsItself = startsOnBootValue;
  const ok = running;
  let headline;
  if (running) headline = "You're all set";
  else headline = "Constellation didn't start";
  const bootWarning = (ok && !startsItself)
    ? "Constellation is running now, but it won't start by itself every time this computer turns on."
    : null;
  const startupNote = startsOnBootValue
    ? "Constellation starts whenever this computer turns on."
    : startsOnLoginValue
      ? "Constellation starts when this user signs in."
      : null;
  const retryHint = ok ? null
    : "Open \"Show details\" for what to try next, then click Try again.";
  return { ok, running, startsItself, startsOnLogin: startsOnLoginValue,
    startsOnBoot: startsOnBootValue, startupNote, headline, bootWarning, retryHint,
    background: !!background };
}

function finishCopy({ startsOnLogin, startsOnBoot, background, backgroundStarted }) {
  const startup = startsOnBoot
    ? "Constellation starts by itself whenever this computer turns on."
    : startsOnLogin
      ? "Constellation starts when this user signs in."
      : "Constellation is running now, but it won't start by itself next time.";
  const progress = background
    ? "Your newest photos are in; the rest arrive overnight on their own."
    : backgroundStarted
      ? "Background processing started for this session. Keep this computer on and signed in, and check Progress."
      : "Background processing is not verified. Keep this computer on and use Progress to continue.";
  return { startup, progress };
}

const API = { MIN_RAM_GB, MIN_FREE_GB, hardwareFloor, pickDriveFor, recommendMode,
  storageMath, validatePin, validateBackupTarget, finishUrls, defaultSourceChecked,
  finishClaims, finishCopy, isTrustedLocalUrl, WALL_URL, wallUrl, validateLibraryRoot, startReady };
// Node (main process, tests) and the wizard page (plain <script>) share this file.
if (typeof module !== "undefined" && module.exports) module.exports = API;
else if (typeof window !== "undefined") window.decide = API;
