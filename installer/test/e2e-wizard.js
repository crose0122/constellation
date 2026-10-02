// Drive the PACKAGED installer (dist/linux-unpacked) with Playwright's Electron
// support through the one-question-per-screen flow: scan for photos
// -> where to keep the copy (refusals, then the default) -> PIN (refusals) ->
// backup -> Start my sky, with the real backend preparing the library and
// running the first sweep. Isolated HOME and fake drives so nothing real is
// read or touched; Ollama, the login service and the server start are stubbed.
const path = require("path"), fs = require("fs"), os = require("os");
const OUT = process.argv[2];
const results = [];
const check = (n, c, x = "") => { results.push(!!c); console.log(`${c ? "PASS" : "FAIL"} ${n}${x ? "  " + x : ""}`); };

// Chromium's Linux sandbox failures: a misconfigured SUID chrome-sandbox
// helper, or a host without usable (unprivileged) user/PID namespaces. Any
// other launch failure is a real defect and must not be masked by retrying
// with the sandbox disabled.
const SANDBOX_LAUNCH_ERROR =
  /chrome-sandbox|SUID sandbox|setuid_sandbox|No usable sandbox|user namespace|new namespace|namespaces? (?:supported|not supported|unavailable)|zygote_host_impl_linux|credentials\.cc/i;
function isSandboxLaunchError(error) {
  const message = typeof error === "string" ? error : error && error.message;
  return typeof message === "string" && SANDBOX_LAUNCH_ERROR.test(message);
}

async function main() {
  const { _electron: electron } = require("playwright");
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-home-"));
  let app;
  try {
    await runWizard(electron, home, (a) => { app = a; });
  } finally {
    if (app) await app.close().catch(() => {});
    // Remove only the HOME this run created; never sweep other runs' dirs.
    fs.rmSync(home, { recursive: true, force: true });
  }
  console.log(`${results.filter(Boolean).length}/${results.length} passed`);
  return results.every(Boolean) ? 0 : 1;
}

async function runWizard(electron, home, track) {
  const exe = path.resolve(__dirname, "..", "dist/linux-unpacked/constellation-setup");
  const env = Object.fromEntries(Object.entries({ ...process.env, HOME: home })
    .filter(([k]) => !k.startsWith("MEMORYVAULT_") && k !== "CONSTELLATION_SCREEN_MODEL"));
  // Exercise the shipped sandbox policy first. --no-sandbox is allowed only
  // when this test host lacks usable user namespaces/SUID sandbox support.
  let app;
  try {
    app = await electron.launch({ executablePath: exe, env });
    track(app);
    const probe = await app.firstWindow();
    await probe.title();
  } catch (error) {
    if (app) await app.close().catch(() => {});
    track(undefined);
    if (!isSandboxLaunchError(error)) throw error;
    console.log("note: sandbox unavailable on this host; retrying smoke with --no-sandbox");
    app = await electron.launch({ executablePath: exe, args: ["--no-sandbox"], env });
    track(app);
  }
  // Never let a test reach the real Ollama, install a login service or leave
  // a server running: stub those IPC handlers in the app's main process.
  await app.evaluate(({ ipcMain }) => {
    for (const ch of ["install", "enableDescribe"]) {
      ipcMain.removeHandler(ch);
      ipcMain.handle(ch, async () => ({ ok: false, error: "downloads disabled in this test" }));
    }
    ipcMain.removeHandler("finish");
    ipcMain.handle("finish", async () => ({ ok: false, headline: "Constellation didn't start.",
      error: "server start disabled in this test" }));
  });
  // Only the synthetic HOME is ever scanned: fake drives (a big local disk and
  // a USB drive that does not exist), never this machine's real mounts.
  await app.evaluate(({ nativeImage }, home) => {
    const cache = process.mainModule.constructor._cache;
    const key = Object.keys(cache).find((k) => /[\\/]scan\.js$/.test(k) && !k.includes("node_modules"));
    const scan = cache[key].exports;
    const fake = [{ path: "/", freeGB: 900, totalGB: 1000, type: "fixed" },
      { path: "/media/test/USB", label: "USB", freeGB: 1800, totalGB: 2000, removable: true, type: "removable" }];
    scan.scanStorage = async () => ({ drives: fake, photoCandidates: [] });
    const orig = scan.fullScan;
    scan.fullScan = async () => { const r = await orig(); r.storage = { drives: fake, photoCandidates: [] }; return r; };
    // synthetic photos: noisy JPEGs well over the scan's 40 KB photo floor
    const fs = require("fs"), path = require("path");
    const dir = path.join(home, "Pictures", "Family");
    fs.mkdirSync(dir, { recursive: true });
    for (let i = 0; i < 24; i++) {
      const w = 480, h = 360, buf = Buffer.alloc(w * h * 4);
      for (let p = 0; p < buf.length; p += 4) { buf[p] = (p * (i + 3)) % 251; buf[p + 1] = (p >> 7) % 253; buf[p + 2] = (i * 37 + (p >> 3)) % 255; buf[p + 3] = 255; }
      fs.writeFileSync(path.join(dir, `IMG_${1000 + i}.jpg`), nativeImage.createFromBitmap(buf, { width: w, height: h }).toJPEG(92));
    }
    fs.mkdirSync(path.join(home, "backup"), { recursive: true });
  }, home);
  const w = await app.firstWindow();
  await w.reload();
  const dialogAnswer = (p) => app.evaluate(({ dialog }, p) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [p] }); }, p);

  await w.waitForSelector("text=Where are your photos?");
  check("first screen asks one question with two choices",
    await w.isVisible("text=Let's scan for your photos") && await w.isVisible("text=I'll pick a folder"));
  await w.screenshot({ path: `${OUT}/w1-where.png` });

  await w.click("#cScan");
  await w.waitForSelector("text=We found photos", { timeout: 60000 });
  const found = await w.innerText("#main");
  check("scan found the Pictures folder with its photos", /Pictures[\s\S]*24 photos/.test(found), found.slice(0, 160));
  check("Pictures pre-ticked with the reason shown", /Ticked because this is where your computer keeps photos/.test(found));
  check("scan never listed a real drive", !/Drive\b/.test(found));
  await w.screenshot({ path: `${OUT}/w2-found.png` });
  await w.click("#next");

  await w.waitForSelector("text=Where should Constellation keep its copy?");
  await dialogAnswer(path.join(home, "Pictures"));
  await w.click("#k2");
  await w.waitForSelector("text=overlaps a folder your photos come from");
  check("library inside a photo folder is refused", await w.isDisabled("#next"));
  await w.click("#k1");
  await w.waitForFunction(() => !document.getElementById("next").disabled);
  check("the default new folder is accepted", true);
  await w.screenshot({ path: `${OUT}/w3-keep.png` });
  await w.click("#next");

  await w.waitForSelector("text=Pick a family PIN.");
  const typePin = async (a, b) => { await w.fill("#pin1", a); await w.dispatchEvent("#pin1", "input");
    await w.fill("#pin2", b); await w.dispatchEvent("#pin2", "input"); };
  await typePin("1111", "1111");
  check("weak PIN refused", /less easy to guess/.test(await w.innerText("#pinmsg")) && await w.isDisabled("#next"));
  await typePin("4827", "4828");
  check("mismatched PIN refused", /don't match/.test(await w.innerText("#pinmsg")) && await w.isDisabled("#next"));
  await typePin("4827", "4827");
  await w.click("#next");

  await w.waitForSelector("text=Pick a backup drive.");
  await w.click("#bSkip");
  check("skipping the backup states the consequence", await w.isVisible("text=your sky has one copy"));
  const backupPath = path.join(home, "backup");
  await dialogAnswer(backupPath);
  await w.click("#bOther");
  await w.waitForSelector("text=Backups go to");
  await w.screenshot({ path: `${OUT}/w4-backup.png` });
  await w.click("#next");                                   // Start my sky

  await w.waitForSelector("text=server start disabled in this test", { timeout: 120000 });
  check("prepare + first sweep ran, then the (stubbed) server start was reported honestly", true);
  const lib = path.join(home, "Constellation", "library");
  check("PIN hash written in the library, never the PIN",
    fs.existsSync(`${lib}/.auth/pin.json`) && !fs.readFileSync(`${lib}/.auth/pin.json`, "utf8").includes("4827"));
  check("family certificate in the library", fs.existsSync(`${lib}/.tls/ca.pem`));
  const cfgText = fs.readFileSync(path.join(home, "Constellation", ".env"), "utf8");
  check("config names the chosen backup and the photo source, not the PIN",
    cfgText.includes(`BACKUP_TARGET=${backupPath}`) && cfgText.includes(`SOURCES=${path.join(home, "Pictures")}`) && !cfgText.includes("4827"));
  check("AI descriptions are off unless the family said yes", /^MEMORYVAULT_VISION=off$/m.test(cfgText));
  check("config points at the bundled screener", /NSFW_ONNX_PATH=.*resources\/models\/nsfw-screen\.onnx/.test(cfgText));
  const originals = fs.existsSync(`${lib}/originals`) ? fs.readdirSync(`${lib}/originals`, { recursive: true }).length : 0;
  check("the first sweep brought photos into the library", originals > 0, `${originals} entries`);
  check("the originals are untouched", fs.readdirSync(path.join(home, "Pictures", "Family")).length === 24);
  await w.screenshot({ path: `${OUT}/w5-sky-stubbed.png` });
}

module.exports = { isSandboxLaunchError };

if (require.main === module) {
  main().then((code) => process.exit(code))
    .catch((e) => { console.error("crashed:", e.message); process.exit(2); });
}
