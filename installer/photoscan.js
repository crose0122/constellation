"use strict";
// "Let's scan for your photos" — find where this family's photos actually are.
//
// Read-only: it lists folders and looks at file names, sizes and dates. It
// never opens, copies, moves or changes anything. The answer is a short list
// of *places* a person would recognise — "Pictures", "USB drive › Family
// 2019" — each with how many photos, which years, and a few samples, so the
// family can tick the right ones instead of guessing at folder names.
//
// A place is a top-level folder of a root (the home folder, or an attached
// drive) whose whole subtree holds at least MIN_PHOTOS photos. Loose photos
// directly in a drive's root become a place of their own.
const fs = require("fs");
const path = require("path");

const IMAGE_EXT = new Set(["jpg", "jpeg", "png", "heic", "heif", "webp", "gif", "tif", "tiff",
  "bmp", "dng", "cr2", "cr3", "nef", "arw", "raf", "orf", "rw2"]);
const VIDEO_EXT = new Set(["mp4", "mov", "m4v", "3gp", "avi", "mkv", "mts"]);
// Formats a desktop can show as a thumbnail without extra codecs.
const SAMPLE_EXT = new Set(["jpg", "jpeg", "png", "webp", "gif", "bmp"]);
const MIN_PHOTOS = 20;
// Folders families actually use for photos: always listed if they hold any.
const WELL_KNOWN = new Set(["pictures", "photos", "dcim", "camera roll", "camera", "onedrive",
  "icloud photos", "iclouddrive", "google photos", "my pictures"]);
// Icons, UI assets and thumbnails are tiny; a real photo or screenshot isn't.
const MIN_PHOTO_BYTES = 40 * 1024;
// A folder containing any of these is software, not a family's photos — on a
// developer's machine every checkout is full of image assets.
const PROJECT_MARKERS = new Set([".git", "package.json", "cargo.toml", "go.mod", "pyproject.toml",
  "setup.py", "pom.xml", "build.gradle", "cmakelists.txt", "makefile", "gemfile", "composer.json",
  ".svn", ".hg", "androidmanifest.xml"]);

// Folders that hold program files, caches or game assets, never a family's
// photos. Matched against one folder name, case-insensitively.
const SKIP_NAMES = new Set(["node_modules", "appdata", "library", "snap", "flatpak",
  "program files", "program files (x86)", "programdata", "windows", "$recycle.bin",
  "system volume information", "recovery", "lost+found", "venv", ".venv", "site-packages",
  "__pycache__", "steam", "steamapps", "steamlibrary", "android", "go", "anaconda3",
  "miniconda3", "dist", "build", "target", "vendor", "cache", "caches", "tmp", "temp",
  "thumbnails", "icons", "wallpapers", "backgrounds"]);

function ext(name) {
  const i = name.lastIndexOf(".");
  return i < 0 ? "" : name.slice(i + 1).toLowerCase();
}

function skipDir(name) {
  return name.startsWith(".") || name.startsWith("$") || SKIP_NAMES.has(name.toLowerCase());
}

function plausibleYear(y) { return y >= 1990 && y <= new Date().getFullYear() + 1; }

// The same folder reached by a second path (a bind mount: /data/home can be
// /home itself) must not be scanned, or counted, twice.
async function dirId(p, io) {
  try { const st = await io.promises.stat(p); return `${st.dev}:${st.ino}`; } catch { return null; }
}

function normalize(p) { return path.resolve(p).replace(/[\\/]+$/, "") || path.sep; }
function isUnder(p, parent) {
  const a = normalize(p), b = normalize(parent);
  return a === b || a.startsWith(b + path.sep);
}

// Evenly spread picks, so four samples are not four frames of one burst.
function spread(list, n) {
  if (list.length <= n) return list.slice();
  const out = [];
  for (let i = 0; i < n; i++) out.push(list[Math.floor((i * (list.length - 1)) / (n - 1))]);
  return out;
}

// Walk one subtree within the shared budget. Returns counts + candidates.
async function walk(dir, ctx) {
  const acc = { photos: 0, videos: 0, minYear: Infinity, maxYear: -Infinity, sampleCands: [] };
  const stack = [dir];
  while (stack.length) {
    if (ctx.outOfBudget()) { ctx.partial = true; break; }
    const cur = stack.pop();
    if (ctx.excluded(cur)) continue;
    const id = await dirId(cur, ctx.io);
    if (id && ctx.seenDirs.has(id)) continue;
    if (id) ctx.seenDirs.add(id);
    let names;
    try { names = await ctx.io.promises.readdir(cur, { withFileTypes: true }); } catch { continue; }
    if (names.some((n) => PROJECT_MARKERS.has(n.name.toLowerCase()))) { ctx.projects++; continue; }
    ctx.dirs++;
    try {
      for (const ent of names) {
        if (++ctx.entries > ctx.maxEntries) { ctx.partial = true; break; }
        const full = path.join(cur, ent.name);
        if (ent.isDirectory()) {
          if (!skipDir(ent.name)) stack.push(full);
          continue;
        }
        if (!ent.isFile()) continue;
        const e = ext(ent.name);
        const isImg = IMAGE_EXT.has(e);
        if (!isImg && !VIDEO_EXT.has(e)) continue;
        let st = null;
        try { st = await ctx.io.promises.stat(full); } catch { continue; }
        if (isImg && st.size < MIN_PHOTO_BYTES) continue;
        if (isImg) acc.photos++; else acc.videos++;
        const mtime = st.mtime;
        if (mtime && plausibleYear(mtime.getFullYear())) {
          const y = mtime.getFullYear();
          if (y < acc.minYear) acc.minYear = y;
          if (y > acc.maxYear) acc.maxYear = y;
        }
        if (isImg && SAMPLE_EXT.has(e) && acc.sampleCands.length < 400) {
          acc.sampleCands.push({ path: full, t: mtime ? mtime.getTime() : 0 });
        }
      }
    } catch { /* a folder vanished mid-read: skip it */ }
    ctx.onProgress({ dirs: ctx.dirs, current: cur });
  }
  return acc;
}

function toPlace(placePath, root, acc, nSamples = 4) {
  const samples = spread(acc.sampleCands.sort((a, b) => a.t - b.t), nSamples).map((s) => s.path);
  return {
    path: placePath,
    root: root.path,
    rootLabel: root.label,
    name: placePath === root.path ? "Loose photos" : path.basename(placePath),
    photos: acc.photos,
    videos: acc.videos,
    firstYear: Number.isFinite(acc.minYear) ? acc.minYear : null,
    lastYear: Number.isFinite(acc.maxYear) ? acc.maxYear : null,
    samples,
  };
}

// roots: [{ path, label }]  exclude: paths never to read (library, backup, data dir)
async function scanForPhotos({ roots, exclude = [], budgetMs = 25000, maxEntries = 400000,
  minPhotos = MIN_PHOTOS, onProgress = () => {}, io = fs, now = Date.now } = {}) {
  const start = now();
  const ex = exclude.filter(Boolean).map(normalize);
  const ctx = {
    io, dirs: 0, entries: 0, projects: 0, seenDirs: new Set(), maxEntries, partial: false, onProgress,
    outOfBudget: () => now() - start > budgetMs,
    excluded: (p) => ex.some((x) => isUnder(p, x)),
  };
  const places = [];
  const seedRoots = async () => {
    for (const r of roots || []) { const id = r && r.path ? await dirId(r.path, io) : null; if (id) ctx.seenDirs.add(id); }
  };
  await seedRoots();
  const skipped = [];
  const seenRoots = [];
  // every root's own identity, so an alias of one root inside another
  // (/data/home being /home) is skipped wherever it turns up
  const rootIds = new Set();
  for (const r of roots || []) { const id = r && r.path ? await dirId(r.path, io) : null; if (id) rootIds.add(id); }
  for (const root of roots || []) {
    if (!root || !root.path) continue;
    const rp = normalize(root.path);
    if (seenRoots.includes(rp)) continue;
    seenRoots.push(rp);
    if (ctx.excluded(rp)) { skipped.push(root.label || rp); continue; }
    let entries;
    try { entries = await io.promises.readdir(rp, { withFileTypes: true }); }
    catch { continue; }
    // loose photos directly in the root
    const loose = { photos: 0, videos: 0, minYear: Infinity, maxYear: -Infinity, sampleCands: [] };
    for (const ent of entries) {
      if (ctx.outOfBudget()) { ctx.partial = true; break; }
      const full = path.join(rp, ent.name);
      if (ent.isDirectory()) {
        if (skipDir(ent.name)) continue;
        // another root nested inside this one (a drive mounted under home) is
        // scanned as its own root, not as a folder of this one
        if ((roots || []).some((r) => r && normalize(r.path) === normalize(full))) continue;
        const cid = await dirId(full, io);
        if (cid && rootIds.has(cid)) continue;
        if (ctx.excluded(full)) { skipped.push(path.basename(full)); continue; }
        const acc = await walk(full, ctx);
        const known = WELL_KNOWN.has(ent.name.toLowerCase());
        if (acc.photos >= minPhotos || (known && acc.photos > 0)) {
          places.push({ ...toPlace(full, { ...root, path: rp }, acc), wellKnown: known });
        }
      } else if (ent.isFile()) {
        const e = ext(ent.name);
        if (IMAGE_EXT.has(e)) {
          let st = null;
          try { st = await io.promises.stat(full); } catch { continue; }
          if (st.size < MIN_PHOTO_BYTES) continue;
          const y = st.mtime.getFullYear();
          if (plausibleYear(y)) { loose.minYear = Math.min(loose.minYear, y); loose.maxYear = Math.max(loose.maxYear, y); }
          loose.photos++;
          if (SAMPLE_EXT.has(e) && loose.sampleCands.length < 400) loose.sampleCands.push({ path: full, t: 0 });
        } else if (VIDEO_EXT.has(e)) loose.videos++;
      }
    }
    if (loose.photos >= minPhotos) places.push(toPlace(rp, { ...root, path: rp }, loose));
  }
  // well-known photo folders first, then the biggest
  places.sort((a, b) => (!!b.wellKnown - !!a.wellKnown) || (b.photos - a.photos));
  return { places, partial: ctx.partial, dirs: ctx.dirs, projects: ctx.projects, skipped, ms: now() - start };
}

// "I'll pick a folder": the same summary for one folder the family chose.
async function summarizeFolder(dir, { exclude = [], budgetMs = 15000, maxEntries = 400000,
  samples = 12, onProgress = () => {}, io = fs, now = Date.now } = {}) {
  const start = now();
  const ex = exclude.filter(Boolean).map(normalize);
  const d = normalize(dir);
  const ctx = {
    io, dirs: 0, entries: 0, projects: 0, seenDirs: new Set(), maxEntries, partial: false, onProgress,
    outOfBudget: () => now() - start > budgetMs,
    excluded: (p) => ex.some((x) => isUnder(p, x)),
  };
  const acc = await walk(d, ctx);
  return { place: toPlace(d, { path: path.dirname(d), label: path.basename(d) }, acc, samples),
    partial: ctx.partial };
}

// Where to look: the home folder, then attached data drives (never system
// mounts). drives: scan.scanStorage().drives.
function scanRoots(home, drives = [], platform = process.platform) {
  const roots = [{ path: home, label: "Home" }];
  for (const d of drives || []) {
    const p = d && d.path;
    if (!p) continue;
    if (platform === "win32") {
      if (d.type === "network") continue;
      roots.push({ path: p, label: d.label ? `${d.label} (${p.replace(/\\$/, "")})` : p.replace(/\\$/, "") });
      continue;
    }
    if (p === "/" || /^\/(boot|snap|var|usr|tmp|proc|sys|dev|etc|opt|srv)(\/|$)/.test(p)) continue;
    if (/^\/run(\/|$)/.test(p) && !/^\/run\/media\//.test(p)) continue;   // /run/user, /run/lock…
    if (isUnder(home, p)) continue;               // the drive that holds home itself
    roots.push({ path: p, label: d.label || path.basename(p) || p });
  }
  return roots;
}

module.exports = { scanForPhotos, summarizeFolder, scanRoots, MIN_PHOTOS, IMAGE_EXT, VIDEO_EXT,
  SAMPLE_EXT, skipDir };
