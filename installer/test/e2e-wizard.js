// Drive the PACKAGED installer (dist/linux-unpacked) with Playwright's Electron
// support: welcome -> real system scan -> storage (validation errors, then a
// valid PIN + backup) -> screenshots. Stops before Downloads (that pulls 6 GB
// and installs Ollama system-wide). Isolated HOME so nothing real is touched.
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
  // Never let a test reach the real Ollama: replace the download handler in
  // the app's main process before the wizard can call it.
  await app.evaluate(({ ipcMain }) => {
    ipcMain.removeHandler("install");
    ipcMain.handle("install", async () => ({ ok: false, error: "downloads disabled in this test" }));
  });
  const w = await app.firstWindow();
  await w.waitForSelector("text=Welcome to Constellation");
  check("welcome screen", await w.isVisible("text=Nothing is sent to anyone"));
  await w.screenshot({ path: `${OUT}/w1-welcome.png` });

  await w.click("#next");
  await w.waitForSelector("text=Here's what I found", { timeout: 60000 });
  const body = await w.innerText("main");
  check("scan says no GPU needed", /doesn't need a graphics card/.test(body));
  check("scan shows memory + free space", /Memory/.test(body) && /Free space for photos/.test(body));
  await w.screenshot({ path: `${OUT}/w2-scan.png` });

  await w.click("text=Show details");
  check("Show details pane", await w.isVisible("#techPanel >> text=AI model"));
  await w.click("text=Show details");

  if (await w.isEnabled("#next")) {
    await w.click("#next");
    await w.waitForSelector("text=Where should your photos live?");
    // library on the big data disk (the default under $HOME is on the small root disk here)
    await w.fill("#lib", (process.env.E2E_LIB || "/tmp/cst-e2e-lib"));
    await w.dispatchEvent("#lib", "change");
    await w.fill("#pin1", "1111"); await w.fill("#pin2", "1111");
    await w.click("#next");
    await w.waitForTimeout(400);
    check("weak PIN refused", /less easy to guess/.test(await w.innerText("#pinErr")));
    check("missing backup refused", /Pick a drive for backups/.test(await w.innerText("#bkErr")));
    const math = await w.innerText("#math");
    check("storage math in plain words", /GB free holds about .* photos/.test(math), math);
    await w.fill("#pin1", "4827"); await w.fill("#pin2", "4828");
    await w.click("#next"); await w.waitForTimeout(300);
    check("mismatched PIN refused", /don't match/.test(await w.innerText("#pinErr")));
    check("still on storage (nothing written)", await w.isVisible("text=Where should your photos live?"));
    await w.screenshot({ path: `${OUT}/w3-storage-errors.png`, fullPage: true });
    const backup = await w.$('[data-bk]');
    check("a backup drive is offered", !!backup);
    const backupPath = backup ? await backup.getAttribute("data-bk") : "";
    const srcTicked = backupPath ? await w.$(`[data-src="${backupPath}"]:checked`) : null;
    check("removable drive not pre-ticked as a photo source", !srcTicked);
    if (backup) {
      await backup.check();
      await w.fill("#pin1", "4827"); await w.fill("#pin2", "4827");
      await w.click("#next");
      await w.waitForSelector("text=Downloading", { timeout: 60000 }).catch(async (e) => {
        console.log("  pinErr:", await w.innerText("#pinErr").catch(() => "?"), "| bkErr:", await w.innerText("#bkErr").catch(() => "?"), "| libErr:", await w.innerText("#libErr").catch(() => "?"));
        throw e;
      });
      await w.waitForSelector("text=downloads disabled in this test", { timeout: 20000 });
      check("valid PIN + backup -> prepare ran -> Downloads step", true);
      const lib = (process.env.E2E_LIB || "/tmp/cst-e2e-lib");
      check("PIN hash written in the chosen library", fs.existsSync(`${lib}/.auth/pin.json`) &&
        !fs.readFileSync(`${lib}/.auth/pin.json`, "utf8").includes("4827"));
      check("family certificate in the chosen library", fs.existsSync(`${lib}/.tls/ca.pem`));
      const env = fs.readFileSync(`${home}/Constellation/.env`, "utf8");
      check("config names the selected backup drive, not the PIN", env.includes(`BACKUP_TARGET=${backupPath}`) && !env.includes("4827"));
      check("config points at the bundled screener", /NSFW_ONNX_PATH=.*resources\/models\/nsfw-screen\.onnx/.test(env));
      await w.screenshot({ path: `${OUT}/w4-downloads.png` });
    }
  } else {
    check("scan allowed continuing", false, "next disabled (RAM floor?)");
  }
}

module.exports = { isSandboxLaunchError };

if (require.main === module) {
  main().then((code) => process.exit(code))
    .catch((e) => { console.error("crashed:", e.message); process.exit(2); });
}
