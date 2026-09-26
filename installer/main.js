// Constellation Setup — Electron main process.
// Owns the wizard window and bridges the renderer to the scan/setup engines.
"use strict";
const { app, BrowserWindow, ipcMain, dialog, shell } = require("electron");
const path = require("path");
const os = require("os");
const scan = require("./scan");
const setup = require("./setup");
const autostart = require("./autostart");
const decide = require("./decide");
const { finishInstall } = require("./finish-install");
const DATA_DIR = () => path.join(app.getPath("home"), "Constellation");

let win;
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
  win.loadFile(path.join(__dirname, "ui", "index.html"));
}

app.whenReady().then(createWindow);
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
  try { return await setup.prepare(BACKEND_DIR, DATA_DIR(), cfg); }
  catch (e) { return { ok: false, error: String((e && e.message) || e) }; }
});

// --- IPC: install Ollama + pull the model (streams progress) ---------------
ipcMain.handle("install", async (_e, cfg) => {
  const send = (p) => win && win.webContents.send("progress", p);
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
  const send = (p) => win && win.webContents.send("progress", p);
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
  const send = (p) => win && win.webContents.send("progress", p);
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

ipcMain.handle("openUrl", (_e, url) => shell.openExternal(url));
ipcMain.handle("defaults", () => ({
  libraryRoot: path.join(os.homedir(), "Constellation", "library"),
}));
