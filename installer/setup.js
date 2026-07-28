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
  const e = { ...process.env };
  if (mode === "cpu") e.OLLAMA_NUM_GPU = "0";
  return e;
}
async function installOllama(cfg, onStatus) {
  if (typeof cfg === "function") { onStatus = cfg; cfg = {}; }  // back-compat
  const env = serveEnv(cfg && cfg.mode);
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
        res.on("end", () => { onStatus({ phase: "model", pct: 1, msg: "Model ready on your GPU." }); resolve(true); });
      });
    req.on("error", reject);
    req.write(body); req.end();
  });
}

// --- write config + launch -------------------------------------------------
function writeConfig(dir, cfg) {
  fs.mkdirSync(dir, { recursive: true });
  const env = [
    `MEMORYVAULT_LIBRARY_ROOT=${cfg.libraryRoot}`,
    `MEMORYVAULT_VISION_MODEL=${cfg.model}`,
    `MEMORYVAULT_OLLAMA_URL=${OLLAMA}/api/generate`,
    `MEMORYVAULT_VAULT_MODE=${cfg.vaultMode || "dir"}`,
    cfg.mode === "cpu" ? "OLLAMA_NUM_GPU=0" : "",
    cfg.sources && cfg.sources.length ? `MEMORYVAULT_SOURCES=${cfg.sources.join(";")}` : "",
  ].filter(Boolean).join("\n") + "\n";
  fs.writeFileSync(path.join(dir, ".env"), env);
  return path.join(dir, ".env");
}

// Launch the backend. Prefers a bundled backend executable shipped with the
// app (set BACKEND_CMD at build time); falls back to Docker Compose if that's
// how this machine runs it. Returns the child so the app can manage it.
function launchStack(appDir, cfg, onStatus) {
  const bundled = process.env.CONSTELLATION_BACKEND ||
    path.join(appDir, "backend", IS_WIN ? "memoryvault-brain.exe" : "memoryvault-brain");
  if (fs.existsSync(bundled)) {
    onStatus({ phase: "launch", msg: "Starting Constellation…" });
    return spawn(bundled, ["brain"], { env: { ...process.env, ...envFromCfg(cfg) }, stdio: "ignore" });
  }
  onStatus({ phase: "launch", msg: "Starting Constellation via Docker…" });
  return spawn(IS_WIN ? "docker.exe" : "docker",
    ["compose", "up", "-d"], { cwd: appDir, stdio: "ignore", windowsHide: true });
}
function envFromCfg(cfg) {
  return {
    MEMORYVAULT_LIBRARY_ROOT: cfg.libraryRoot,
    MEMORYVAULT_VISION_MODEL: cfg.model,
    MEMORYVAULT_OLLAMA_URL: OLLAMA + "/api/generate",
    MEMORYVAULT_VAULT_MODE: cfg.vaultMode || "dir",
  };
}

module.exports = { ollamaRunning, ollamaInstalled, installOllama, pullModel, writeConfig, launchStack };
