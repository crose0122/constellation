"use strict";

// BrowserWindow is a single local-document surface, not a browser. Register
// this boundary before loadFile so no renderer-controlled navigation or popup
// can race initialization.
function installNavigationBoundary(window, intendedDocumentUrl) {
  if (typeof intendedDocumentUrl !== "string" || !intendedDocumentUrl.startsWith("file:///")) {
    throw new TypeError("intended document must be an absolute file URL");
  }
  window.webContents.on("will-navigate", (event, targetUrl) => {
    if (targetUrl !== intendedDocumentUrl) event.preventDefault();
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
}

module.exports = { installNavigationBoundary };