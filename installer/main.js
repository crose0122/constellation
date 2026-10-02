// Constellation Setup — Electron main process.
// Owns the wizard window and bridges the renderer to the scan/setup engines.
"use strict";
const { app, BrowserWindow, ipcMain, dialog, shell, nativeImage } = require("electron");
const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");
const os = require("os");
const scan = require("./scan");
const setup = require("./setup");
const autostart = require("./autostart");
const decide = require("./decide");
const network = require("./network");
const photoscan = require("./photoscan");
const { finishInstall } = require("./finish-install");
const debuglog = require("./debuglog");
const debugstate = require("./debugstate");
const { openWall } = require("./open-url");
const { installNavigationBoundary } = require("./navigation");
debuglog.installConsoleTap();
const DATA_DIR = () => path.join(app.getPath("home"), "Constellation");

let win;
// The port pair this install's server actually got (set by "finish").
let installPorts = { httpPort: 8484, tlsPort: 8485 };
const UI_INDEX = path.join(__dirname, "ui", "index.html");

// Every progress event the wizard shows also lands in the Debug Console ring
// (redacted), so a stuck install can be read back with Ctrl+Shift+D.
function progressSender() {
  return (p) => {
    try {
      if (p && p.msg) {
        const lvl = p.phase === "error" ? "error" : "log";
        debuglog.push(lvl, `[${p.phase || "progress"}] ${p.msg}`);
      }
    } catch { /* never break progress */ }
    if (win) win.webContents.send("progress", p);
  };
}
const APP_DIR = app.isPackaged ? path.dirname(app.getPath("exe")) : __dirname;
// the PyInstaller backend bundle: shipped as extraResources when packaged,
// or the local build output in dev
const BACKEND_DIR = app.isPackaged
  ? path.join(process.resourcesPath, "backend")
  : path.join(__dirname, "..", "scripts", "dist", "memoryvault-brain");

function createWindow() {
  win = new BrowserWindow({
    width: 940, height: 680, minWidth: 820, minHeight: 600,
    backgroundColor: "#010409", title: "Constellation Setup",
    autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, nodeIntegration: false },
  });
  installNavigationBoundary(win, pathToFileURL(UI_INDEX).href);
  win.loadFile(UI_INDEX);
}

app.whenReady().then(() => {
  console.log(`[setup] Constellation Setup ${app.getVersion()} starting (${process.platform}/${process.arch})`);
  createWindow();
});
process.on("uncaughtException", (e) => console.error("[setup] uncaught:", e));
process.on("unhandledRejection", (e) => console.error("[setup] unhandled rejection:", e));
app.on("window-all-closed", () => app.quit());

// --- IPC: hardware/network scan -------------------------------------------
ipcMain.handle("scan", async () => {
  try { return { ok: true, data: await scan.fullScan() }; }
  catch (e) { return { ok: false, error: String(e) }; }
});

// --- IPC: pick a folder (library location / a source) ----------------------
ipcMain.handle("pickFolder", async (_e, title) => {
  const r = await dialog.showOpenDialog(win,
    { title: title || "Choose a folder", properties: ["openDirectory", "createDirectory"] });
  return r.canceled ? null : r.filePaths[0];
});

// --- IPC: "Let's scan for your photos" / "I'll pick a folder" ---------------
// Read-only. Never reads Constellation's own folder or the backup drive.
// Samples become small thumbnails here, so the page never gets file paths
// it could load itself.
function thumbnail(file, height = 96) {
  try {
    const img = nativeImage.createFromPath(file);
    if (img.isEmpty()) return null;
    return img.resize({ height, quality: "good" }).toDataURL();
  } catch { return null; }
}
function withThumbs(place, height) {
  return { ...place, thumbs: (place.samples || []).map((f) => thumbnail(f, height)).filter(Boolean),
    samples: undefined };
}
function scanExclusions(backupTarget) {
  return [DATA_DIR(), path.join(os.homedir(), "Constellation"), backupTarget].filter(Boolean);
}
ipcMain.handle("scanPhotos", async (_e, opts = {}) => {
  try {
    const { drives } = await scan.scanStorage();
    let last = 0;
    const r = await photoscan.scanForPhotos({
      roots: photoscan.scanRoots(os.homedir(), drives),
      exclude: scanExclusions(opts && opts.backupTarget),
      onProgress: (p) => {
        const t = Date.now();
        if (win && t - last > 250) { last = t; win.webContents.send("scanProgress", { dirs: p.dirs }); }
      },
    });
    return { ok: true, partial: r.partial, skipped: r.skipped,
      places: r.places.slice(0, 8).map((pl) => withThumbs(pl, 96)) };
  } catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
});
ipcMain.handle("summarizeFolder", async (_e, dir, opts = {}) => {
  if (typeof dir !== "string" || !path.isAbsolute(dir)) return { ok: false, error: "Pick a folder." };
  try {
    const r = await photoscan.summarizeFolder(dir, { exclude: scanExclusions(opts && opts.backupTarget) });
    return { ok: true, partial: r.partial, place: withThumbs(r.place, 120) };
  } catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
});
ipcMain.handle("libraryCheck", (_e, cfg) => ({ error: setup.libraryLocationError(cfg || {}) }));

// --- IPC: library + family PIN + family certificate ------------------------
ipcMain.handle("prepare", async (_e, cfg) => {
  if (cfg && cfg.pin) debuglog.rememberSecret(String(cfg.pin));
  console.log("[setup] preparing library + family PIN + certificate");
  try {
    const r = await setup.prepare(BACKEND_DIR, DATA_DIR(), cfg);
    if (!r || !r.ok) console.warn("[setup] prepare failed:", (r && r.error) || "unknown");
    return r;
  } catch (e) {
    console.error("[setup] prepare threw:", e);
    return { ok: false, error: String((e && e.message) || e) };
  }
});

// --- IPC: install Ollama + pull the model (streams progress) ---------------
ipcMain.handle("install", async (_e, cfg) => {
  const send = progressSender();
  // Ollama + the vision model exist only for "Describe your photos". Without
  // that yes, nothing is downloaded and nothing touches the graphics card.
  try {
    return await setup.installVision(cfg, send);
  } catch (e) {
    send({ phase: "error", msg: String(e && e.message || e) });
    return { ok: false, error: String(e) };
  }
});

// --- IPC: "Describe your photos" — the family said yes ---------------------
// Download Ollama + the model (the only big download), switch the saved
// config to vision-on so overnight runs include it, then describe what is
// already in the library. Nothing here runs without that explicit yes.
ipcMain.handle("enableDescribe", async (_e, cfg) => {
  const send = progressSender();
  if (!cfg || cfg.describe !== true) return { ok: false, error: "Descriptions weren't turned on." };
  try {
    await setup.installOllama(cfg, send);
    await setup.pullModel(cfg.model, send);
    setup.writeConfig(DATA_DIR(), cfg);
    return setup.startVisionSweep(BACKEND_DIR, cfg);
  } catch (e) {
    send({ phase: "error", msg: String((e && e.message) || e) });
    return { ok: false, error: String((e && e.message) || e) };
  }
});

// --- IPC: write config + run the first sweep -------------------------------
// This is what puts photos in the library. Without it the wizard finishes onto
// an empty star map and the folders chosen on the storage step are never read.
ipcMain.handle("sweep", async (_e, cfg) => {
  const send = progressSender();
  try {
    setup.writeConfig(DATA_DIR(), cfg);
    return await setup.runFirstSweep(BACKEND_DIR, cfg, send);
  } catch (e) {
    send({ phase: "error", msg: String((e && e.message) || e) });
    return { ok: false, error: String(e) };
  }
});

// --- IPC: launch the stack, start the slow stages, open the app ------------
ipcMain.handle("finish", async (_e, cfg) => {
  const send = progressSender();
  try {
    // Never assume 8484 is ours: another Constellation (or anything else) may
    // already hold it, and its wall would pass for ours. Take a free pair.
    const ports = await network.pickPorts();
    if (!ports) {
      return { ok: false, error: "Constellation couldn't find a free network port on this computer." };
    }
    installPorts = ports;
    cfg = { ...cfg, ...ports };
    const caFile = path.join(cfg.libraryRoot, ".tls", "ca.pem");
    return await finishInstall({ cfg, dataDir: DATA_DIR(), backendDir: BACKEND_DIR,
      appDir: APP_DIR, send, deps: {
        writeConfig: setup.writeConfig,
        backendExe: setup.backendExe,
        installAutostart: autostart.install,
        launchStack: setup.launchStack,
        serverUp: setup.serverUp,
        startBackgroundSweep: setup.startBackgroundSweep,
        finishClaims: decide.finishClaims,
        servesOurCa: network.servesOurCa,
        readOurCa: () => fs.readFileSync(caFile, "utf8"),
      } });
  } catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
});

// --- IPC: this machine's LAN address + ports, for the TV / phone links ------
// The private address on the default route — never Docker, VPN or other
// virtual interfaces (network.js) — and the ports the server really got.
ipcMain.handle("lanAddress", () => ({ address: network.lanAddress(), ...installPorts }));

ipcMain.handle("openWall", () => openWall((target) => shell.openExternal(target), installPorts.httpPort));
ipcMain.handle("defaults", () => ({
  libraryRoot: path.join(os.homedir(), "Constellation", "library"),
}));

// --- IPC: Debug Console (Ctrl+Shift+D in the wizard) -----------------------
// Read-only, local-only: the installer's own bundled page is the only frame
// allowed to read it; there is no network route.
ipcMain.handle("debugState", async (e, since) => {
  const url = e && e.senderFrame ? e.senderFrame.url : "";
  if (!debugstate.isTrustedSender(url, UI_INDEX)) return { error: "forbidden" };
  return debugstate.buildState({ since, backendDir: BACKEND_DIR, dataDir: DATA_DIR(),
    exeFn: setup.backendExe });
});
