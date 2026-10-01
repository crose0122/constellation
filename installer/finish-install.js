"use strict";

// Finish orchestration lives outside Electron so startup and rollback behavior
// can be exercised without loading a desktop runtime.
async function finishInstall({ cfg, dataDir, backendDir, appDir, send, deps }) {
  const envFile = deps.writeConfig(dataDir, cfg);
  const exe = deps.backendExe(backendDir);
  let auto = { ok: false, error: "no bundled backend" };
  if (exe) auto = await deps.installAutostart({ exe, envFile, dataDir });

  let launchAttempted = false;
  if (auto.ok && auto.started) {
    launchAttempted = true;
  } else {
    if (!auto.ok) send({ phase: "launch",
      msg: `Start-on-boot not set up (${auto.error}); starting just for now.` });
    deps.launchStack(backendDir, appDir, cfg, send);
    launchAttempted = true;
  }

  let readinessError = null;
  let serverUp = false;
  if (launchAttempted) {
    try { serverUp = await deps.serverUp("127.0.0.1", 8484, 15000); }
    catch (e) { readinessError = String((e && e.message) || e); }
  }
  let rollback = null;
  if (!serverUp && auto.ok && typeof auto.rollback === "function") {
    try { rollback = await auto.rollback(); }
    catch (e) { rollback = { ok: false, error: String((e && e.message) || e) }; }
    auto = { ...auto, ok: false };
  }

  let bg = { ok: false, verified: false };
  if (serverUp) bg = deps.startBackgroundSweep(backendDir, cfg);
  const claims = deps.finishClaims({
    autostartOk: !!auto.ok,
    startsOnLogin: !!auto.startsOnLogin,
    startsOnBoot: !!auto.startsOnBoot,
    started: !!auto.started || launchAttempted,
    serverUp,
    background: !!(bg.ok && bg.verified),
  });
  return {
    ok: claims.ok,
    autostart: !!auto.ok,
    background: !!(bg.ok && bg.verified),
    backgroundStarted: !!bg.ok,
    serverUp,
    error: serverUp ? null : (readinessError || "Constellation readiness check failed."),
    rollbackError: rollback && !rollback.ok ? String(rollback.error || "rollback failed") : null,
    autostartError: auto.ok ? null : String(auto.error || ""),
    ...claims,
  };
}

module.exports = { finishInstall };