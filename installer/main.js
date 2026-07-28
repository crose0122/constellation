// Constellation Setup — Electron main process.
// Owns the wizard window and bridges the renderer to the scan/setup engines.
"use strict";
const { app, BrowserWindow, ipcMain, dialog, shell } = require("electron");
const path = require("path");
const os = require("os");
const scan = require("./scan");
const setup = require("./setup");

let win;
const APP_DIR = app.isPackaged ? path.dirname(app.getPath("exe")) : __dirname;

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

// --- IPC: write config, launch the stack, open the app ---------------------
ipcMain.handle("finish", async (_e, cfg) => {
  const send = (p) => win && win.webContents.send("progress", p);
  try {
    const dataDir = path.join(app.getPath("home"), "Constellation");
    setup.writeConfig(dataDir, cfg);
    setup.launchStack(APP_DIR, cfg, send);
    return { ok: true, url: "http://localhost:8484/menu" };
  } catch (e) { return { ok: false, error: String(e) }; }
});

ipcMain.handle("openUrl", (_e, url) => shell.openExternal(url));
ipcMain.handle("defaults", () => ({
  libraryRoot: path.join(os.homedir(), "Constellation", "library"),
}));
