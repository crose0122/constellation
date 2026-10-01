// Constellation Setup — Electron main process.
// Owns the wizard window and bridges the renderer to the scan/setup engines.
"use strict";
const { app, BrowserWindow, ipcMain, dialog, shell } = require("electron");
const path = require("path");
const { pathToFileURL } = require("url");
const os = require("os");
const scan = require("./scan");
const setup = require("./setup");
const autostart = require("./autostart");
const decide = require("./decide");
const { finishInstall } = require("./finish-install");
const debuglog = require("./debuglog");
const debugstate = require("./debugstate");
const { openWall } = require("./open-url");
const { installNavigationBoundary } = require("./navigation");
debuglog.installConsoleTap();
const DATA_DIR = () => path.join(app.getPath("home"), "Constellation");

let win;
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
  try {
    await setup.installOllama(cfg, send);
    await setup.pullModel(cfg.model, send);
    return { ok: true };
  } catch (e) {
    send({ phase: "error", msg: String(e && e.message || e) });
    return { ok: false, error: String(e) };
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
    return await finishInstall({ cfg, dataDir: DATA_DIR(), backendDir: BACKEND_DIR,
      appDir: APP_DIR, send, deps: {
        writeConfig: setup.writeConfig,
        backendExe: setup.backendExe,
        installAutostart: autostart.install,
        launchStack: setup.launchStack,
        serverUp: setup.serverUp,
        startBackgroundSweep: setup.startBackgroundSweep,
        finishClaims: decide.finishClaims,
      } });
  } catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
});

// --- IPC: this machine's LAN address, for the TV / phone step --------------
function lanAddress() {
  for (const ifaces of Object.values(os.networkInterfaces() || {})) {
    for (const i of ifaces || []) {
      if (i.family === "IPv4" && !i.internal) return i.address;
    }
  }
  return null;
}

ipcMain.handle("lanAddress", () => lanAddress());

ipcMain.handle("openWall", () => openWall((target) => shell.openExternal(target)));
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
