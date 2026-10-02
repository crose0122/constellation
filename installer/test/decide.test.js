"use strict";
// node --test installer/test   (no Electron needed)
const test = require("node:test");
const assert = require("node:assert/strict");
const d = require("../decide");

const drives = [
  { path: "/", freeGB: 40 },
  { path: "/mnt/library", freeGB: 900 },
  { path: "/mnt/synthetic-backup", freeGB: 3600, removable: true },
];

test("hardware floor: 8 GB RAM and 200 GB free where the library goes", () => {
  assert.equal(d.hardwareFloor({ ramGB: 16 }, drives, "/mnt/library/Constellation").ok, true);
  const lowRam = d.hardwareFloor({ ramGB: 4 }, drives, "/mnt/library/Constellation");
  assert.equal(lowRam.ok, false);
  assert.match(lowRam.problems[0], /4 GB of memory/);
  const lowDisk = d.hardwareFloor({ ramGB: 16 }, drives, "/home/example/Constellation");
  assert.equal(lowDisk.ok, false, "home is on / with 40 GB — must be refused");
  assert.match(lowDisk.problems[0], /40 GB free/);
  assert.equal(lowDisk.shoppingList, undefined);
  assert.match(lowDisk.hardwareGuidance, /8 GB.*200 GB/i);
  assert.match(lowDisk.hardwareGuidance, /does not provide a hardware shopping link/i);
});

test("only the exact fixed wall destination may be opened outside the installer", async () => {
  assert.equal(d.isTrustedLocalUrl("http://localhost:8484/wall"), true);
  const hostile = [
    "https://localhost:8484/wall", "http://localhost:8485/wall",
    "http://localhost:8484/", "http://localhost:8484/wall/",
    "http://localhost:8484/wall?next=1", "http://localhost:8484/wall#x",
    "http://user@localhost:8484/wall", "http://LOCALHOST:8484/wall",
    "http://localhost:8484/%77all", "http://127.0.0.1:8484/wall",
    `http://${[192, 168, 50, 7].join(".")}:8484/wall`,
    `http://${[169, 254, 169, 254].join(".")}/latest/meta-data/`,
    " http://localhost:8484/wall ", "javascript:alert(1)",
  ];
  for (const url of hostile) assert.equal(d.isTrustedLocalUrl(url), false, url);

  const { openWall } = require("../open-url");
  const opened = [];
  await openWall(async (url) => opened.push(url));
  assert.deepEqual(opened, ["http://localhost:8484/wall"], "the opener owns the fixed target");
});

test("Electron navigation boundary is installed before load and denies hostile navigation", () => {
  const fs = require("node:fs");
  const path = require("node:path");
  const { installNavigationBoundary } = require("../navigation");
  let navigateHandler;
  let openHandler;
  const fake = { webContents: {
    on: (event, handler) => { if (event === "will-navigate") navigateHandler = handler; },
    setWindowOpenHandler: (handler) => { openHandler = handler; },
  } };
  const intended = "file:///opt/constellation/installer/ui/index.html";
  installNavigationBoundary(fake, intended);
  assert.equal(openHandler({ url: intended }).action, "deny");
  for (const hostile of [
    "https://example.invalid/", "http://localhost:8484/wall",
    `http://${[169, 254, 169, 254].join(".")}/latest/meta-data/`, "file:///etc/passwd",
    intended + "?query=1", intended + "#fragment",
  ]) {
    let prevented = false;
    navigateHandler({ preventDefault: () => { prevented = true; } }, hostile);
    assert.equal(prevented, true, hostile);
  }
  let prevented = false;
  navigateHandler({ preventDefault: () => { prevented = true; } }, intended);
  assert.equal(prevented, false, "only the intended local document may navigate");

  const main = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
  const boundary = main.indexOf("installNavigationBoundary(win, pathToFileURL(UI_INDEX).href)");
  const load = main.indexOf("win.loadFile(UI_INDEX)");
  assert.notEqual(boundary, -1, "createWindow must install the navigation boundary for the wizard document");
  assert.notEqual(load, -1, "createWindow must load the bundled wizard document");
  assert.ok(boundary < load,
    "navigation handlers must be registered before loading renderer content");
  assert.equal(main.split("loadFile(").length - 1, 1, "exactly one renderer load, and it is guarded");
  assert.equal(main.split("loadURL(").length - 1, 0, "the wizard never loads a remote URL");
});

test("installer document has a strict local-only CSP", () => {
  const fs = require("node:fs");
  const path = require("node:path");
  const html = fs.readFileSync(path.join(__dirname, "../ui/index.html"), "utf8");
  assert.match(html, /Content-Security-Policy/);
  for (const directive of ["default-src 'none'", "script-src 'self'", "object-src 'none'",
    "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'", "connect-src 'none'"]) {
    assert.ok(html.includes(directive), directive);
  }
});

test("installer contains no parked external hardware navigation", () => {
  const fs = require("node:fs");
  const path = require("node:path");
  for (const relative of ["../decide.js", "../open-url.js", "../ui/wizard.js", "../main.js", "../preload.js"]) {
    const source = fs.readFileSync(path.join(__dirname, relative), "utf8");
    assert.doesNotMatch(source, /constellation\.family\/hardware/i);
    assert.doesNotMatch(source, /recommended (?:hardware )?(?:list|shopping)/i);
  }
  const mainSource = fs.readFileSync(path.join(__dirname, "../main.js"), "utf8");
  const preloadSource = fs.readFileSync(path.join(__dirname, "../preload.js"), "utf8");
  assert.doesNotMatch(mainSource + preloadSource, /["']openUrl["']/,
    "the renderer must not have a generic external-URL IPC channel");
  for (const relative of ["../../docs/CONSTELLATION-V2-BUILD-PLAN.md", "../../docs/CONSTELLATION-APP-SPEC-V2.md"]) {
    const source = fs.readFileSync(path.join(__dirname, relative), "utf8");
    assert.doesNotMatch(source, /shopping[- ]list page/i);
  }
});

test("exactly at the floor passes; one below fails", () => {
  const at = [{ path: "/", freeGB: 200 }];
  assert.equal(d.hardwareFloor({ ramGB: 8 }, at, "/x").ok, true);
  assert.equal(d.hardwareFloor({ ramGB: 7 }, at, "/x").ok, false);
  assert.equal(d.hardwareFloor({ ramGB: 8 }, [{ path: "/", freeGB: 199 }], "/x").ok, false);
});

test("drive lookup picks the longest matching mount, Windows too", () => {
  assert.equal(d.pickDriveFor("/mnt/library/lib", drives).path, "/mnt/library");
  assert.equal(d.pickDriveFor("/mnt/libraryarchive", drives).path, "/", "prefix must be a whole path segment");
  const win = [{ path: "C:\\", freeGB: 50 }, { path: "D:\\", freeGB: 800 }];
  assert.equal(d.pickDriveFor("D:\\Photos\\Lib", win).path, "D:\\");
  assert.equal(d.pickDriveFor("c:\\users\\x", win).path, "C:\\");
});

test("no GPU needed: CPU is the default and the only choice without a real accelerator", () => {
  const none = d.recommendMode({ accel: "cpu", name: null }, { ramGB: 8 });
  assert.equal(none.mode, "cpu");
  assert.deepEqual(none.modes, ["cpu"]);
  assert.doesNotMatch(none.note, /slow|GPU/i, "no-GPU copy must not sound like a failure");
  const igpu = d.recommendMode({ accel: "cpu", name: "Intel UHD 630", integrated: true }, { ramGB: 16 });
  assert.equal(igpu.mode, "cpu");
});

test("a real accelerator is offered, CPU stays selectable", () => {
  const nv = d.recommendMode({ accel: "cuda", vramGB: 8, name: "RTX 3070" }, { ramGB: 32 });
  assert.equal(nv.mode, "gpu");
  assert.deepEqual(nv.modes, ["gpu", "cpu"]);
  const small = d.recommendMode({ accel: "cuda", vramGB: 2, name: "GTX 950" }, { ramGB: 16 });
  assert.equal(small.mode, "cpu", "a 2 GB card is not worth offering");
});

test("storage math is plain words with round numbers", () => {
  assert.equal(d.storageMath(900), "900 GB free holds about 225,000 photos, or 20 hours of phone video.");
});

test("PIN rules", () => {
  assert.equal(d.validatePin("4827", "4827"), null);
  assert.match(d.validatePin("12"), /at least 4/);
  assert.match(d.validatePin("1111"), /less easy/);
  assert.match(d.validatePin("1234"), /less easy/);
  assert.match(d.validatePin("4827", "4828"), /don't match/);
});

test("backup must be on a different drive from the library", () => {
  assert.match(d.validateBackupTarget("", "/mnt/library/lib", drives), /Pick a drive/);
  assert.match(d.validateBackupTarget("/mnt/library/bk", "/mnt/library/lib", drives), /same drive/);
  assert.equal(d.validateBackupTarget("/mnt/synthetic-backup/bk", "/mnt/library/lib", drives), null);
});

test("finish URLs: frame on http, private app on https, CA on http", () => {
  const u = d.finishUrls("192.0.2.10");
  assert.equal(u.wall, "http://192.0.2.10:8484/wall");
  assert.equal(u.app, "https://192.0.2.10:8485/menu");
  assert.equal(u.ca, "http://192.0.2.10:8484/ca.pem");
});

test("the backup drive can't also be a photo source", () => {
  assert.match(d.validateBackupTarget("/mnt/synthetic-backup", "/mnt/library/lib", drives, ["/mnt/synthetic-backup"]), /photo source/);
  assert.match(d.validateBackupTarget("/mnt/synthetic-backup/bk", "/mnt/library/lib", drives, ["/mnt/synthetic-backup"]), /photo source/);
  assert.equal(d.validateBackupTarget("/mnt/synthetic-backup/bk", "/mnt/library/lib", drives, ["/mnt/library/pics"]), null);
});

test("removable drives are offered as sources but not pre-ticked", () => {
  assert.equal(d.defaultSourceChecked("/mnt/synthetic-backup", drives), false);
  assert.equal(d.defaultSourceChecked("/mnt/library/Pictures", drives), true);
});

test("wall link follows the install's real port and trusts nothing else", () => {
  const d = require("../decide");
  assert.equal(d.wallUrl(8584), "http://localhost:8584/wall");
  assert.equal(d.isTrustedLocalUrl("http://localhost:8584/wall", 8584), true);
  assert.equal(d.isTrustedLocalUrl("http://localhost:8484/wall", 8584), false, "the old fixed port is not ours");
  assert.equal(d.isTrustedLocalUrl("http://localhost:8584/menu", 8584), false);
  assert.equal(d.wallUrl(80), d.WALL_URL, "privileged/invalid ports fall back to the default");
  assert.equal(d.wallUrl("8584"), d.WALL_URL);
});
