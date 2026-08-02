// Constellation Setup — secure IPC bridge (context-isolated).
"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("setup", {
  scan: () => ipcRenderer.invoke("scan"),
  pickFolder: (title) => ipcRenderer.invoke("pickFolder", title),
  install: (cfg) => ipcRenderer.invoke("install", cfg),
  sweep: (cfg) => ipcRenderer.invoke("sweep", cfg),
  finish: (cfg) => ipcRenderer.invoke("finish", cfg),
  lanAddress: () => ipcRenderer.invoke("lanAddress"),
  defaults: () => ipcRenderer.invoke("defaults"),
  openUrl: (url) => ipcRenderer.invoke("openUrl", url),
  onProgress: (cb) => ipcRenderer.on("progress", (_e, p) => cb(p)),
});
