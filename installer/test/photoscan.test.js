"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const ps = require("../photoscan");

const PHOTO = Buffer.alloc(60 * 1024, 7);     // big enough to count as a photo
const ICON = Buffer.alloc(2 * 1024, 7);       // an icon: never counted

function put(root, rel, buf = PHOTO, year = 2019) {
  const f = path.join(root, rel);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  fs.writeFileSync(f, buf);
  const t = new Date(`${year}-06-01T12:00:00Z`);
  fs.utimesSync(f, t, t);
  return f;
}

function tree() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "photoscan-"));
  const home = path.join(tmp, "home");
  const drive = path.join(tmp, "USB");
  for (let i = 0; i < 5; i++) put(home, `Pictures/Camera/IMG_${i}.jpg`, PHOTO, 2014 + i);
  for (let i = 0; i < 25; i++) put(drive, `Family 2019/trip/DSC${i}.JPG`, PHOTO, 2019);
  put(drive, "Family 2019/trip/clip.mp4", PHOTO, 2019);
  for (let i = 0; i < 40; i++) put(home, `code/app/assets/icon${i}.png`, PHOTO);  // a software project…
  put(home, "code/app/package.json", Buffer.from("{}"));                          // …says so
  for (let i = 0; i < 40; i++) put(home, `Downloads/ui/icon${i}.png`, ICON);       // tiny: not photos
  for (let i = 0; i < 30; i++) put(home, `.cache/thumbs/t${i}.jpg`, PHOTO);        // hidden: skipped
  for (let i = 0; i < 30; i++) put(home, `Constellation/library/originals/o${i}.jpg`, PHOTO); // our own library
  return { tmp, home, drive };
}

test("scan finds family photo places, with counts, years and samples", async () => {
  const { tmp, home, drive } = tree();
  try {
    const r = await ps.scanForPhotos({ roots: [{ path: home, label: "Home" }, { path: drive, label: "USB" }],
      exclude: [path.join(home, "Constellation")] });
    const byName = Object.fromEntries(r.places.map((p) => [p.name, p]));
    assert.deepEqual(Object.keys(byName).sort(), ["Family 2019", "Pictures"]);
    assert.equal(byName.Pictures.photos, 5, "well-known folder listed below the minimum");
    assert.equal(byName.Pictures.wellKnown, true);
    assert.equal(byName.Pictures.firstYear, 2014);
    assert.equal(byName.Pictures.lastYear, 2018);
    assert.equal(byName["Family 2019"].photos, 25);
    assert.equal(byName["Family 2019"].videos, 1);
    assert.equal(byName["Family 2019"].rootLabel, "USB");
    assert.equal(byName["Family 2019"].samples.length, 4);
    assert.equal(r.places[0].name, "Pictures", "well-known photo folders come first");
    assert.equal(r.projects >= 1, true, "the software project was recognised and skipped");
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test("scan never reads excluded folders (the library itself, the backup drive)", async () => {
  const { tmp, home, drive } = tree();
  try {
    const r = await ps.scanForPhotos({ roots: [{ path: home, label: "Home" }, { path: drive, label: "USB" }],
      exclude: [path.join(home, "Constellation"), drive] });
    assert.equal(r.places.some((p) => p.path.startsWith(drive)), false);
    assert.equal(r.places.some((p) => p.path.includes("Constellation")), false);
    assert.ok(r.skipped.includes("USB"));
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test("scan does not count the same folder twice through a second path", async () => {
  const { tmp, home } = tree();
  try {
    const data = path.join(tmp, "data");
    fs.mkdirSync(data);
    fs.symlinkSync(home, path.join(data, "home"));     // stands in for a second path to home
    const r = await ps.scanForPhotos({ roots: [{ path: home, label: "Home" }, { path: data, label: "data" }],
      exclude: [path.join(home, "Constellation")] });
    assert.equal(r.places.filter((p) => p.name === "Pictures").length, 1);
    assert.equal(r.places.some((p) => p.rootLabel === "data"), false);
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test("scan stops at its budget and says it was partial", async () => {
  const { tmp, home } = tree();
  try {
    let t = 0;
    const r = await ps.scanForPhotos({ roots: [{ path: home, label: "Home" }], budgetMs: 5, now: () => (t += 10) });
    assert.equal(r.partial, true);
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test("summarizeFolder describes exactly the folder that was picked", async () => {
  const { tmp, drive } = tree();
  try {
    const r = await ps.summarizeFolder(path.join(drive, "Family 2019"));
    assert.equal(r.place.photos, 25);
    assert.equal(r.place.name, "Family 2019");
    assert.equal(r.place.samples.length, 12);
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test("scanRoots: home plus data drives, never system mounts or the drive holding home", () => {
  const drives = [{ path: "/" }, { path: "/boot/efi" }, { path: "/home" }, { path: "/snap/core/1" },
    { path: "/run/media/kim/USB", label: "USB", removable: true }, { path: "/data", label: "data" },
    { path: "/run/user/1000" }];
  const r = ps.scanRoots("/home/example", drives, "linux").map((x) => x.path);
  assert.deepEqual(r, ["/home/example", "/run/media/kim/USB", "/data"]);
  const w = ps.scanRoots("C:\\Users\\Kim", [{ path: "D:\\", label: "Photos", type: "fixed" },
    { path: "Z:\\", type: "network" }], "win32").map((x) => x.label);
  assert.deepEqual(w, ["Home", "Photos (D:)"]);
});

// A fake filesystem, to control exactly what stat/readdir return.
function fakeFs(tree, ids = {}) {
  const dir = (p) => tree[p];
  return { promises: {
    async readdir(p) {
      if (!dir(p)) throw new Error("ENOENT");
      return dir(p).map((e) => ({ name: e.name, isDirectory: () => !!e.dir, isFile: () => !e.dir }));
    },
    async stat(p) {
      const isDir = !!tree[p];
      return { dev: 1, ino: ids[p] != null ? ids[p] : Math.abs([...p].reduce((h, c) => (h * 31 + c.charCodeAt(0)) | 0, 7)),
        size: 60 * 1024, mtime: new Date("2019-06-01T12:00:00Z"), isDirectory: () => isDir };
    },
  } };
}
const files = (n, prefix = "p") => Array.from({ length: n }, (_, i) => ({ name: `${prefix}${i}.jpg` }));

test("scan enforces its budget INSIDE one huge folder and says it was partial", async () => {
  // Repro (2026-10-02): 50,000 photos in one folder at 1 ms per stat
  // ran ~54 s against a 500 ms budget and reported partial:false.
  let t = 0;
  const io = fakeFs({ "/h": [{ name: "Big", dir: true }], "/h/Big": files(5000) });
  io.promises.stat = ((orig) => async (p) => { t += 1; return orig(p); })(io.promises.stat);
  const r = await ps.scanForPhotos({ roots: [{ path: "/h", label: "Home" }], io, budgetMs: 200, now: () => t });
  assert.equal(r.partial, true);
  assert.ok(t < 600, `stopped near the budget, not after all 5000 stats (t=${t})`);
});

test("scan reads a folder once when two paths lead to it (same device and inode)", async () => {
  const io = fakeFs({ "/h": [{ name: "A", dir: true }, { name: "B", dir: true }],
    "/h/A": files(30), "/h/B": files(30) }, { "/h/A": 5000, "/h/B": 5000 });
  const r = await ps.scanForPhotos({ roots: [{ path: "/h", label: "Home" }], io });
  assert.equal(r.places.length, 1, "the alias must not be listed or counted twice");
});

// Mutation check (2026-10-04): the walk-time ctx.excluded guard had no test —
// removing it left the whole photoscan suite green. It is the ONLY thing that
// stops the walk descending INTO an excluded folder, and summarizeFolder
// ("I'll pick a folder") has no root-level check of its own, so a family who
// picks their home folder — with the backup drive mounted under it — would get
// the backup's photos counted as import sources.
test("the walk never descends into an excluded folder (summarizeFolder)", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "photoscan-excl-"));
  try {
    const home = path.join(tmp, "home");
    const backup = path.join(home, "USB-Backup");          // a drive mounted under home
    for (let i = 0; i < 30; i++) put(backup, `Family 2019/DSC${i}.jpg`);
    put(home, "Pictures/Camera/IMG_1.jpg");
    const r = await ps.summarizeFolder(home, { exclude: [backup] });
    assert.equal(r.place.photos, 1, "the excluded backup drive was walked into");
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});

test("the scan excludes an excluded folder nested inside a scanned one, and says so", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "photoscan-nested-"));
  try {
    const home = path.join(tmp, "home");
    const backup = path.join(home, "USB-Backup");
    for (let i = 0; i < 30; i++) put(backup, `Family 2019/DSC${i}.jpg`);
    put(home, "Pictures/Camera/IMG_1.jpg");
    const r = await ps.scanForPhotos({ roots: [{ path: home, label: "Home" }], exclude: [backup] });
    assert.equal(r.places.some((p) => p.path === backup || p.path.startsWith(backup + path.sep)),
      false, "an excluded folder must never appear as a place");
    assert.equal(r.places.some((p) => p.photos >= 30), false, "its photos must not be counted");
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});
