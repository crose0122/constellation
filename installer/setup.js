// Constellation Setup — install/launch orchestration.
// Handles the parts after the hardware scan: installing Ollama, pulling the
// vision model onto the GPU (with streaming progress), writing config, and
// starting the Constellation stack. Progress is reported via a callback so the
// wizard can render live bars.
"use strict";
const os = require("os");
const fs = require("fs");
const path = require("path");
const https = require("https");
const { spawn, execFile } = require("child_process");

const IS_WIN = process.platform === "win32";
const IS_MAC = process.platform === "darwin";
const OLLAMA = "http://127.0.0.1:11434";

function sh(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    execFile(cmd, args, { timeout: opts.timeout || 20000, windowsHide: true },
      (err, out) => resolve({ ok: !err, out: String(out || "") }));
  });
}

// --- Ollama presence -------------------------------------------------------
async function ollamaRunning() {
  return new Promise((resolve) => {
    const req = https.request; // placeholder to keep https referenced
    const http = require("http");
    const r = http.get(OLLAMA + "/api/version", (res) => {
      res.resume(); resolve(res.statusCode === 200);
    });
    r.on("error", () => resolve(false));
    r.setTimeout(2500, () => { r.destroy(); resolve(false); });
  });
}

async function ollamaInstalled() {
  const r = await sh(IS_WIN ? "where" : "which", ["ollama"]);
  return r.ok && r.out.trim().length > 0;
}

// --- download a file with progress ----------------------------------------
function download(url, dest, onProgress) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    const get = (u) => https.get(u, (res) => {
      if ([301, 302, 307, 308].includes(res.statusCode)) return get(res.headers.location);
      if (res.statusCode !== 200) return reject(new Error("HTTP " + res.statusCode));
      const total = parseInt(res.headers["content-length"] || "0", 10);
      let got = 0;
      res.on("data", (c) => { got += c.length; if (total) onProgress(got / total); });
      res.pipe(file);
      file.on("finish", () => file.close(() => resolve(dest)));
    }).on("error", reject);
    get(url);
  });
}

// --- install Ollama --------------------------------------------------------
const OLLAMA_URLS = {
  win32: "https://ollama.com/download/OllamaSetup.exe",
  darwin: "https://ollama.com/download/Ollama-darwin.zip",
};
function serveEnv(mode) {
  // honor a CPU override: OLLAMA_NUM_GPU=0 keeps inference off the GPU
  const e = {};
  if (mode === "cpu") e.OLLAMA_NUM_GPU = "0";
  return e;
}
async function installOllama(cfg, onStatus) {
  if (typeof cfg === "function") { onStatus = cfg; cfg = {}; }  // back-compat
  const env = { ...process.env, ...serveEnv(cfg && cfg.mode) };
  const startServe = () => spawn("ollama", ["serve"],
    { detached: true, stdio: "ignore", windowsHide: true, env }).unref();
  if (await ollamaRunning()) { onStatus({ phase: "ollama", pct: 1, msg: "Ollama already running." }); return; }
  if (await ollamaInstalled()) {
    onStatus({ phase: "ollama", pct: 1, msg: "Ollama installed — starting service…" });
    startServe();
    await waitFor(ollamaRunning, 20000);
    return;
  }
  if (IS_WIN) {
    const dest = path.join(os.tmpdir(), "OllamaSetup.exe");
    onStatus({ phase: "ollama", pct: 0, msg: "Downloading Ollama…" });
    await download(OLLAMA_URLS.win32, dest, (p) =>
      onStatus({ phase: "ollama", pct: p * 0.9, msg: `Downloading Ollama… ${Math.round(p * 100)}%` }));
    onStatus({ phase: "ollama", pct: 0.92, msg: "Installing Ollama…" });
    await sh(dest, ["/VERYSILENT", "/NORESTART"], { timeout: 180000 });
  } else if (IS_MAC) {
    onStatus({ phase: "ollama", pct: 0.5, msg: "Please install Ollama from ollama.com, then continue." });
  } else {
    onStatus({ phase: "ollama", pct: 0.2, msg: "Installing Ollama…" });
    await sh("bash", ["-lc", "curl -fsSL https://ollama.com/install.sh | sh"], { timeout: 300000 });
  }
  startServe();
  onStatus({ phase: "ollama", pct: 0.98, msg: "Starting Ollama…" });
  await waitFor(ollamaRunning, 30000);
  onStatus({ phase: "ollama", pct: 1, msg: "Ollama ready." });
}

function waitFor(fn, ms) {
  const t0 = Date.now();
  return new Promise((resolve) => {
    const tick = async () => {
      if (await fn()) return resolve(true);
      if (Date.now() - t0 > ms) return resolve(false);
      setTimeout(tick, 1500);
    };
    tick();
  });
}

// --- pull the vision model onto the GPU (streaming progress) ---------------
function pullModel(model, onStatus) {
  const http = require("http");
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ model, stream: true });
    const req = http.request(OLLAMA + "/api/pull",
      { method: "POST", headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) } },
      (res) => {
        let buf = "";
        res.on("data", (chunk) => {
          buf += chunk;
          let nl;
          while ((nl = buf.indexOf("\n")) >= 0) {
            const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
            if (!line) continue;
            try {
              const j = JSON.parse(line);
              if (j.error) return reject(new Error(j.error));
              const pct = j.total ? (j.completed || 0) / j.total : undefined;
              onStatus({ phase: "model", pct, msg: j.status + (pct != null ? ` ${Math.round(pct * 100)}%` : "") });
            } catch { /* partial */ }
          }
        });
        res.on("end", () => { onStatus({ phase: "model", pct: 1, msg: "Ready." }); resolve(true); });
      });
    req.on("error", reject);
    req.write(body); req.end();
  });
}

// --- write config + launch -------------------------------------------------
// The config file. Only paths and choices — never the PIN (the backend keeps
// a scrypt hash of it in the library's .auth dir) and never a key.
function configLines(cfg) {
  return [
    `MEMORYVAULT_LIBRARY_ROOT=${cfg.libraryRoot}`,
    `MEMORYVAULT_VISION_MODEL=${cfg.model}`,
    `MEMORYVAULT_OLLAMA_URL=${OLLAMA}/api/generate`,
    `MEMORYVAULT_VAULT_MODE=${cfg.vaultMode || "dir"}`,
    cfg.mode === "cpu" ? "OLLAMA_NUM_GPU=0" : "",
    cfg.sources && cfg.sources.length ? `MEMORYVAULT_SOURCES=${cfg.sources.join(";")}` : "",
    cfg.backupTarget ? `MEMORYVAULT_BACKUP_TARGET=${cfg.backupTarget}` : "",
    `MEMORYVAULT_AUTO_UPDATE=${cfg.updates === false ? "0" : "1"}`,
    screenModelPath() ? `MEMORYVAULT_NSFW_ONNX_PATH=${screenModelPath()}` : "",
  ].filter(Boolean);
}
function writeConfig(dir, cfg) {
  for (const v of [cfg.libraryRoot, cfg.backupTarget, ...(cfg.sources || [])]) {
    if (v && /[\r\n]/.test(v)) throw new Error("a folder name contains a line break");
  }
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, ".env");
  fs.writeFileSync(file, configLines(cfg).join("\n") + "\n", { mode: 0o600 });
  return file;
}

// The nearest existing parent of each folder must be writable, or setup would
// fail later with a raw Python traceback instead of a sentence.
function firstUnwritable(dirs) {
  for (const d of dirs) {
    if (!d) continue;
    let p = path.resolve(d);
    while (!fs.existsSync(p) && path.dirname(p) !== p) p = path.dirname(p);
    try { fs.accessSync(p, fs.constants.W_OK); } catch { return d; }
  }
  return null;
}

// The backend must write the PIN hash and the family keys INSIDE the library
// the family just chose. A stray MEMORYVAULT_* variable left in the parent
// environment (an old install, a dev shell) would silently redirect them
// somewhere else — so every backend call starts from a clean slate and only
// the installer's own settings are passed through.
function cleanEnv() {
  const e = {};
  for (const [k, v] of Object.entries(process.env)) if (!k.startsWith("MEMORYVAULT_")) e[k] = v;
  return e;
}

// Library + family PIN + family certificate, before anything is downloaded, so
// a mistake here (bad folder, backend missing) shows up in seconds, not after
// a 6 GB download.
async function prepare(backendDir, dataDir, cfg) {
  const exe = backendExe(backendDir);
  if (!exe) return { ok: false, error: "The Constellation program files are missing. Reinstall and try again." };
  const unwritable = firstUnwritable([cfg.libraryRoot, cfg.backupTarget]);
  if (unwritable) {
    return { ok: false, field: "lib",
      error: `Constellation can't save files in ${unwritable}. Pick a folder you own, or ask whoever set up this computer to give you access.` };
  }
  const envFile = writeConfig(dataDir, cfg);
  const env = { ...cleanEnv(), ...envFromCfg(cfg) };
  const run = (args, input) => new Promise((resolve) => {
    const c = spawn(exe, args, { env, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
    let err = "";
    c.stderr.on("data", (b) => { err += b; });
    c.on("error", (e) => resolve({ ok: false, error: String(e.message || e) }));
    c.on("close", (code) => resolve({ ok: code === 0, error: err.trim().split("\n").pop() }));
    if (input != null) c.stdin.end(input + "\n"); else c.stdin.end();
  });
  let r = await run(["init"]);
  if (!r.ok) return { ok: false, field: "lib", detail: r.error,
    error: "Couldn't create the library. Open \"Show details\" for the technical reason." };
  r = await run(["pin", "set", "--stdin"], cfg.pin);          // stdin, never argv
  if (!r.ok) return { ok: false, error: r.error || "Couldn't save the family PIN." };
  r = await run(["tls", "init"]);
  if (!r.ok) return { ok: false, error: `Couldn't create the family certificate: ${r.error}` };
  return { ok: true, envFile };
}

// Resolve the bundled backend executable, or null when this machine runs the
// stack via Docker instead.
function backendExe(backendDir) {
  const exe = process.env.CONSTELLATION_BACKEND ||
    path.join(backendDir, IS_WIN ? "memoryvault-brain.exe" : "memoryvault-brain");
  return fs.existsSync(exe) ? exe : null;
}

// Launch the backend. Prefers a bundled backend executable shipped with the
// app (set BACKEND_CMD at build time); falls back to Docker Compose if that's
// how this machine runs it. Returns the child so the app can manage it.
function launchStack(backendDir, appDir, cfg, onStatus) {
  const exe = backendExe(backendDir);
  if (exe) {
    onStatus({ phase: "launch", msg: "Starting Constellation…" });
    return spawn(exe, ["constellation"], {
      env: { ...cleanEnv(), ...serveEnv(cfg && cfg.mode), ...envFromCfg(cfg) },
      stdio: "ignore", windowsHide: true, detached: true }).unref();
  }
  // fallback for a dev machine that runs the stack via Docker
  onStatus({ phase: "launch", msg: "Starting Constellation via Docker…" });
  return spawn(IS_WIN ? "docker.exe" : "docker",
    ["compose", "up", "-d"], { cwd: appDir, stdio: "ignore", windowsHide: true });
}
// The screening model ships with the installer (resources/models) so a new
// install can screen photos with no GPU and no extra download. Without it
// every photo stops at the screening step and the wall stays empty.
function screenModelPath() {
  const candidates = [
    process.env.CONSTELLATION_SCREEN_MODEL,
    process.resourcesPath && path.join(process.resourcesPath, "models", "nsfw-screen.onnx"),
    path.join(__dirname, "models", "nsfw-screen.onnx"),
  ].filter(Boolean);
  return candidates.find((p) => fs.existsSync(p)) || null;
}
function envFromCfg(cfg) {
  const screen = screenModelPath();
  return {
    ...(screen ? { MEMORYVAULT_NSFW_ONNX_PATH: screen } : {}),
    MEMORYVAULT_LIBRARY_ROOT: cfg.libraryRoot,
    MEMORYVAULT_VISION_MODEL: cfg.model,
    MEMORYVAULT_OLLAMA_URL: OLLAMA + "/api/generate",
    MEMORYVAULT_VAULT_MODE: cfg.vaultMode || "dir",
    MEMORYVAULT_AUTO_UPDATE: cfg.updates === false ? "0" : "1",
    ...(cfg.backupTarget ? { MEMORYVAULT_BACKUP_TARGET: cfg.backupTarget } : {}),
  };
}

// --- the first sweep -------------------------------------------------------
// Installing the software is not the job; seeing your photos is. Until this
// existed the wizard wrote a config, started the server and opened a star map
// with nothing in it — the folders picked on the storage step were never read
// by anything.
//
// The sweep is split in two because the stages have wildly different costs:
//
//   foreground  init -> discover -> ingest -> curate   (no model involved)
//       Hashing and EXIF only. Minutes, not hours, and when it finishes the
//       library genuinely has photos in it, so the app opens with content.
//
//   background  screen -> tag -> geocode -> describe -> faces -> edges
//       Every model-bound stage. On a CPU-only box tagging a large library is
//       an overnight job, so it must not hold the wizard hostage — it runs
//       detached and the app's own /progress page reports it filling in.
//
// Stage order matches docker/entrypoint.sh, which is the canonical chain.
// Spec B6: the first sweep reads only the newest photos so the sky is alive
// the same evening; everything older is ingested by the overnight chain.
const FIRST_SWEEP_LIMIT = 2000;
const FOREGROUND_STAGES = [
  { args: ["init"], label: "Preparing the library" },
  // discover is expanded per source folder below
  { args: ["ingest", "--recent-first", "--progress-json", "--limit", String(FIRST_SWEEP_LIMIT)],
    label: "Reading your newest photos", counts: true },
  { args: ["curate"], label: "Setting aside screenshots and junk" },
  { args: ["bursts"], label: "Keeping the best shot of each burst" },
];

const BACKGROUND_STAGES = [
  ["ingest"],                                   // the archive backfill
  ["curate"],
  ["bursts"],                                   // keep the sharpest, park the rest
  ["screen"], ["tag"], ["geocode"], ["describe"],
  ["faces", "scan"], ["faces", "cluster"], ["edges"],
];

// One stdout line from the backend -> a count for the patient sky, or null.
function parseProgress(line) {
  if (!line || line[0] !== "{") return null;
  try {
    const j = JSON.parse(line);
    const p = j && j.progress;
    if (p && Number.isFinite(p.done) && Number.isFinite(p.total)) return { done: p.done, total: p.total };
  } catch { /* not a progress line */ }
  return null;
}

function runStage(exe, args, env, onLine) {
  return new Promise((resolve) => {
    const child = spawn(exe, args, { env, windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"] });
    let tail = "";
    const feed = (buf) => {
      tail += buf;
      let nl;
      while ((nl = tail.indexOf("\n")) >= 0) {
        const line = tail.slice(0, nl).trim();
        tail = tail.slice(nl + 1);
        if (line) onLine(line);
      }
    };
    child.stdout.on("data", feed);
    child.stderr.on("data", feed);
    child.on("error", (e) => resolve({ ok: false, error: String(e.message || e) }));
    child.on("close", (code) => resolve({ ok: code === 0, code }));
  });
}

// Run the cheap stages to completion, reporting stage-level progress plus the
// backend's own most recent output line.
async function runFirstSweep(backendDir, cfg, onStatus) {
  const exe = backendExe(backendDir);
  if (!exe) {
    // Docker path: the compose file already exposes an equivalent chain.
    onStatus({ phase: "sweep", pct: 1,
      msg: "Docker install — run `docker compose run --rm memoryvault pipeline`." });
    return { ok: true, skipped: true };
  }
  const env = { ...cleanEnv(), ...serveEnv(cfg && cfg.mode), ...envFromCfg(cfg) };
  const sources = (cfg.sources || []).filter(Boolean);

  const stages = [];
  for (const s of FOREGROUND_STAGES) {
    stages.push(s);
    if (s.args[0] === "init") {
      for (const src of sources) {
        stages.push({ args: ["discover", src, "--kind", "local"],
          label: `Finding photos in ${path.basename(src) || src}` });
      }
    }
  }

  for (let i = 0; i < stages.length; i++) {
    const st = stages[i];
    const base = i / stages.length;
    onStatus({ phase: "sweep", pct: base, msg: st.label + "…" });
    const r = await runStage(exe, st.args, env, (line) => {
      const pr = st.counts ? parseProgress(line) : null;
      if (pr) {
        const frac = pr.total ? pr.done / pr.total : 0;
        onStatus({ phase: "sweep", pct: base + frac / stages.length, count: pr.done,
          msg: `${st.label} — ${pr.done} of ${pr.total}` });
      } else {
        onStatus({ phase: "sweep", pct: base, msg: `${st.label} — ${line.slice(0, 90)}` });
      }
    });
    // `init` must succeed — without a database nothing downstream can run.
    // The rest are best-effort: a single unreadable folder is not a reason to
    // strand someone at a setup wizard with no way forward.
    if (!r.ok && st.args[0] === "init") {
      return { ok: false, error: r.error || `\`init\` failed (exit ${r.code})` };
    }
    if (!r.ok) {
      onStatus({ phase: "sweep", pct: base,
        msg: `${st.label} — finished with warnings; continuing.` });
    }
  }
  onStatus({ phase: "sweep", pct: 1, msg: "Your photos are in." });
  return { ok: true, sources: sources.length };
}

// Kick off the model-bound stages detached, so they survive this window
// closing. One shell holds the chain: each stage is allowed to fail without
// killing the ones after it (`;`), matching entrypoint.sh's `|| true` spirit.
function startBackgroundSweep(backendDir, cfg) {
  const exe = backendExe(backendDir);
  if (!exe) return { ok: false, skipped: true };
  const env = { ...cleanEnv(), ...serveEnv(cfg && cfg.mode), ...envFromCfg(cfg) };
  const q = (s) => IS_WIN ? `"${s}"` : `'${String(s).replace(/'/g, `'\\''`)}'`;
  const chain = BACKGROUND_STAGES
    .map((args) => [exe, ...args].map(q).join(" "))
    .join(IS_WIN ? " & " : "; ");
  const child = IS_WIN
    ? spawn("cmd.exe", ["/c", chain], { env, detached: true, stdio: "ignore", windowsHide: true })
    : spawn("/bin/sh", ["-c", chain], { env, detached: true, stdio: "ignore" });
  child.unref();
  return { ok: true };
}

module.exports = { ollamaRunning, ollamaInstalled, installOllama, pullModel,
  writeConfig, configLines, prepare, launchStack, runFirstSweep, startBackgroundSweep, backendExe,
  parseProgress, FOREGROUND_STAGES, BACKGROUND_STAGES, FIRST_SWEEP_LIMIT, screenModelPath, firstUnwritable };
