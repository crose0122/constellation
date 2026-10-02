// Constellation Setup — Debug Console state (fleet debug-console contract v1).
//
// buildState() is what the wizard's Ctrl+Shift+D overlay polls over IPC:
//   { product, now, log: [entries newer than since], checks: [{label, ok, detail}] }
// Every probe is bounded (the whole thing answers in < 3 s even when Ollama
// hangs). Status only: no PIN, no photo paths, no vault paths.
//
// "Auth": this state never leaves the machine. It is served over IPC to the
// installer's own window only — isTrustedSender() refuses any frame that isn't
// the bundled ui/index.html (no HTTP route exists at all).
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const { pathToFileURL } = require("url");
const debuglog = require("./debuglog");

const OLLAMA_TIMEOUT_MS = 1500;
const DEADLINE_MS = 2500;

function probeOllama(url = "http://127.0.0.1:11434/api/version", timeoutMs = OLLAMA_TIMEOUT_MS) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (r) => { if (!done) { done = true; resolve(r); } };
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (c) => { if (body.length < 4096) body += c; });
      res.on("end", () => {
        let ver = "";
        try { ver = JSON.parse(body).version || ""; } catch { /* ignore */ }
        finish({ label: "Ollama", ok: res.statusCode === 200,
          detail: res.statusCode === 200 ? `running ${ver}`.trim() : `HTTP ${res.statusCode}` });
      });
    });
    req.on("timeout", () => { req.destroy(); finish({ label: "Ollama", ok: false, detail: "timed out" }); });
    req.on("error", (e) => finish({ label: "Ollama", ok: null,
      detail: e.code === "ECONNREFUSED" ? "not running yet" : `unreachable (${e.code || e.message})` }));
  });
}

function checkBackend(backendDir, exeFn) {
  let exe = null;
  try { exe = exeFn ? exeFn(backendDir) : null; } catch { exe = null; }
  return { label: "Backend bundle", ok: !!exe, detail: exe ? "present" : "missing — installer can't set up the library" };
}

function checkDisk(dir) {
  try {
    let d = dir;
    while (d && !fs.existsSync(d) && path.dirname(d) !== d) d = path.dirname(d);
    const s = fs.statfsSync(d);
    const total = s.blocks * s.bsize, free = s.bavail * s.bsize;
    const pct = total ? Math.round(100 * (total - free) / total) : 0;
    return { label: "Disk", ok: pct < 90, detail: `${pct}% used · ${Math.floor(free / 1024 ** 3)} GB free` };
  } catch (e) {
    return { label: "Disk", ok: null, detail: `can't read (${e.code || e.message})` };
  }
}

function checkDataDir(dataDir) {
  const exists = fs.existsSync(dataDir);
  const cfg = exists && fs.existsSync(path.join(dataDir, ".env"));
  return { label: "Config", ok: cfg ? true : null, detail: cfg ? "written" : exists ? "folder ready, config not written yet" : "not created yet" };
}

function checkPlatform() {
  return { label: "Platform", ok: true, detail: `${process.platform}/${process.arch} · ${Math.round(os.totalmem() / 1024 ** 3)} GB RAM` };
}

async function buildState({ since, backendDir, dataDir, exeFn, ollamaUrl } = {}) {
  const timeout = new Promise((r) => setTimeout(() => r(null), DEADLINE_MS));
  const ollama = await Promise.race([probeOllama(ollamaUrl), timeout])
    || { label: "Ollama", ok: null, detail: "timed out" };
  const checks = [
    ollama,
    checkBackend(backendDir, exeFn),
    checkDataDir(dataDir),
    checkDisk(dataDir),
    checkPlatform(),
  ].map((c) => ({ label: c.label, ok: c.ok, detail: debuglog.redact(String(c.detail || "")).slice(0, 200) }));
  return { product: "constellation-setup", now: new Date().toISOString(),
    log: debuglog.entries(typeof since === "string" && since ? since : undefined), checks };
}

// Only the installer's own bundled page may read the log.
function isTrustedSender(frameUrl, uiIndexPath) {
  try {
    return typeof frameUrl === "string" && frameUrl.split("#")[0].split("?")[0] ===
      pathToFileURL(uiIndexPath).href;
  } catch { return false; }
}

module.exports = { buildState, isTrustedSender, probeOllama, DEADLINE_MS };
