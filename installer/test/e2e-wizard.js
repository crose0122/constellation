// Drive the PACKAGED installer (dist/linux-unpacked) with Playwright's Electron
// support: welcome -> real system scan -> storage (validation errors, then a
// valid PIN + backup) -> screenshots. Stops before Downloads (that pulls 6 GB
// and installs Ollama system-wide). Isolated HOME so nothing real is touched.
const { _electron: electron } = require("playwright");
const path = require("path"), fs = require("fs");
const OUT = process.argv[2];
const results = [];
const check = (n, c, x = "") => { results.push(!!c); console.log(`${c ? "PASS" : "FAIL"} ${n}${x ? "  " + x : ""}`); };

(async () => {
  const home = fs.mkdtempSync("/tmp/cst-home-");
  const app = await electron.launch({
    executablePath: path.resolve(__dirname, "..", "dist/linux-unpacked/constellation-setup"),
    args: ["--no-sandbox"],
    env: Object.fromEntries(Object.entries({ ...process.env, HOME: home }).filter(([k]) => !k.startsWith("MEMORYVAULT_") && k !== "CONSTELLATION_SCREEN_MODEL")),
  });
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
    const elements = await w.$('[data-bk$="/Elements"]');
    check("USB backup drive (Elements) offered", !!elements);
    const srcTicked = await w.$('[data-src$="/Elements"]:checked');
    check("removable drive not pre-ticked as a photo source", !srcTicked);
    if (elements) {
      await elements.check();
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
      check("config names the backup drive, not the PIN", /BACKUP_TARGET=\/run\/media\/.*\/Elements/.test(env) && !env.includes("4827"));
      check("config points at the bundled screener", /NSFW_ONNX_PATH=.*resources\/models\/nsfw-screen\.onnx/.test(env));
      await w.screenshot({ path: `${OUT}/w4-downloads.png` });
    }
  } else {
    check("scan allowed continuing", false, "next disabled (RAM floor?)");
  }
  await app.close();
  console.log(`${results.filter(Boolean).length}/${results.length} passed`);
  process.exit(results.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error("crashed:", e.message); process.exit(2); });
