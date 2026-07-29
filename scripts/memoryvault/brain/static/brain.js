/* The Brain — memory graph rendered as living neurons (SPEC.md §9.1).
   Default view: CATEGORY neurons (tag values sized by photo count) joined by
   co-occurrence dendrites, styled after biological neuron micrographs —
   electric-blue somas, organic branching dendrites, dim depth-field behind.
   Tap a category → its photos; tap a photo → the photo-level pathway view.
   Hand-rolled canvas; nothing external is loaded. */

// Performance mode for weak displays (budget Android-TV boxes, etc.). Starts
// on if ?lite is in the URL; otherwise auto-enables when the frame rate tanks.
let LITE = /(?:\?|&)lite\b/.test(location.search);

const REL_COLORS = {
  "same-person": "#e8b64c",
  "same-place": "#5aa2e0",
  "similar": "#8f6ae0",
  "same-event": "#5ec98f",
  "near-time": "#8a8a99",
};
const REL_LABELS = {
  "same-person": "same person",
  "same-place": "same place",
  "similar": "visually similar",
  "same-event": "same day",
  "near-time": "near in time",
};
const BLUE = "#49c6ff";
const BLUE_DIM = "#1b5f8f";

/* chromatic zones (Benzi/flight-patterns style): hue follows the tag
   dimension so color carries meaning, not decoration */
const DIM_COLORS = {
  people: "#ffd27a",        // gold
  pets: "#ffb36b",
  occasion: "#ff5fa8",      // magenta
  milestone: "#ff5fa8",
  emotion: "#b78bff",       // violet
  sentiment: "#b78bff",
  location: "#49c6ff",      // cyan
  activity: "#5ef2c4",      // mint
  season_holiday: "#ff8d66",
  time_of_day: "#7fd4ff",
  place: "#8fe08a",       // map-pin green
  year: "#9db4d0",          // dim steel
  curation: "#8a8a99",
};
const FAMILY_GOLD = "#ffe9b8";
function dimColor(dim) {
  if (dim === "family") return FAMILY_GOLD;
  return DIM_COLORS[dim] || BLUE;
}

const BUILD = "v16-dbg";
function beacon() {
  try {
    const vv = window.visualViewport;
    fetch("/api/dbg?" + new URLSearchParams({
      b: BUILD, w: W, h: H, dpr: window.devicePixelRatio,
      iw: innerWidth, ih: innerHeight,
      cw: document.documentElement.clientWidth,
      ch: document.documentElement.clientHeight,
      vw: vv ? Math.round(vv.width) : -1, vh: vv ? Math.round(vv.height) : -1,
      vs: vv ? vv.scale : -1,
      cssw: canvas.getBoundingClientRect().width.toFixed(0),
      cssh: canvas.getBoundingClientRect().height.toFixed(0),
    }));
  } catch (e) {}
}
const canvas = document.getElementById("brain");
const ctx = canvas.getContext("2d");
let W, H, DPR;
let SCALE = 1; // layout constants are tuned for ~900px; shrink for phones
const bg = document.createElement("canvas");

function resize() {
  // full native density: the frame loop is baked-layer blits now, so
  // cap resolution: 3x DPR on a 4K TV renders ~25M pixels/frame and crushes
  // budget boxes. 2x is plenty; LITE (weak device / ?lite) drops to 1x.
  DPR = Math.min(window.devicePixelRatio || 1, LITE ? 1 : 2);
  // layout viewport (what position:fixed spans) — stable under pinch-zoom,
  // unlike innerWidth/visualViewport which track the zoomed visible region
  W = document.documentElement.clientWidth || window.innerWidth;
  H = document.documentElement.clientHeight || window.innerHeight;
  canvas.width = W * DPR; canvas.height = H * DPR;
  // explicit element sizing: never trust stylesheet timing or desktop-site
  // mode to keep the element from inflating to its bitmap size
  canvas.style.cssText =
    `position:fixed;left:0;top:0;width:${W}px;height:${H}px;`;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  SCALE = Math.max(0.4, Math.min(1, Math.min(W, H) / 900));
  if (typeof SPRITE_SS !== "undefined") SPRITE_SS = Math.max(2, Math.ceil(DPR));
  for (const n of nodes.values()) {
    if (n.baseR) n.r = n.baseR * SCALE;
    n.sprite = null; // radius changed — rebuild cached rendering
  }
  paintBackground();
  if (typeof beacon === "function") setTimeout(beacon, 400);
  if (typeof reheat === "function") reheat();
  if (typeof view !== "undefined" && view === "categories") layoutCategories();
  if (typeof edgeLayerValid !== "undefined") edgeLayerValid = false;
}
window.addEventListener("resize", resize);

// NOTE: initial resize() happens after state init below — resize touches `nodes`.

/* Seeded rng so each neuron's dendrites are stable frame to frame. */
function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}
function hashStr(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) h = ((h ^ str.charCodeAt(i)) * 16777619) >>> 0;
  return h;
}

/* Faint out-of-focus neuron field for depth, repainted only on resize. */
function paintBackground() {
  bg.width = W * DPR; bg.height = H * DPR;
  const b = bg.getContext("2d");
  b.setTransform(DPR, 0, 0, DPR, 0, 0);
  b.clearRect(0, 0, W, H);
  const rand = rng(1337);
  for (let i = 0; i < 26; i++) {
    const x = rand() * W, y = rand() * H;
    const r = 2 + rand() * 6, a = 0.04 + rand() * 0.09;
    b.save();
    b.globalAlpha = a;
    b.strokeStyle = BLUE_DIM;
    b.lineWidth = 0.7;
    const nb = 3 + Math.floor(rand() * 3);
    for (let j = 0; j < nb; j++) {
      const ang = rand() * Math.PI * 2, len = r * (4 + rand() * 7);
      b.beginPath();
      b.moveTo(x, y);
      b.quadraticCurveTo(
        x + Math.cos(ang + 0.4) * len * 0.5, y + Math.sin(ang + 0.4) * len * 0.5,
        x + Math.cos(ang) * len, y + Math.sin(ang) * len);
      b.stroke();
    }
    const g = b.createRadialGradient(x, y, 0, x, y, r * 2.4);
    g.addColorStop(0, BLUE);
    g.addColorStop(1, "transparent");
    b.fillStyle = g;
    b.globalAlpha = a * 2.2;
    b.beginPath(); b.arc(x, y, r * 2.4, 0, Math.PI * 2); b.fill();
    b.restore();
  }
  // particle dust + faint concentric orbital ellipses (Plakhova depth)
  b.save();
  for (let i = 0; i < 160; i++) {
    b.globalAlpha = 0.03 + rand() * 0.1;
    b.fillStyle = rand() > 0.8 ? "#cfe8ff" : BLUE_DIM;
    b.fillRect(rand() * W, rand() * H, 1, 1);
  }
  b.strokeStyle = BLUE_DIM;
  b.lineWidth = 0.6;
  for (let i = 0; i < 3; i++) {
    b.globalAlpha = 0.05;
    b.beginPath();
    b.ellipse(W / 2, H / 2, (W / 2) * (0.55 + i * 0.22),
              (H / 2) * (0.5 + i * 0.22), 0.15 * (i - 1), 0, Math.PI * 2);
    b.stroke();
  }
  b.restore();
}
/* ---------- state ---------- */
let view = "categories";          // categories | photos
const nodes = new Map();          // key/id -> node
let edges = [];                   // {a,b,weight[,relation]}
let focusId = null;
let ambient = location.pathname === "/ambient";
let fireRing = null;
let tick = 0;
let temp = 1; // layout "temperature": cools until the web settles still
var focusNbr = null;                            // Set of node ids lit by focus
/* 3D orbit camera: drag = rotate, inertia glide, tap-focus rotates the
   sphere so the chosen neuron faces the viewer */
var yaw = 0.6, pitch = -0.15, yawV = 0.004, pitchV = 0;
var zoom = 1, zoomT = 1;                       // pinch/wheel camera zoom
var roll = 0;                                  // third axis, idle only
var yawT = null, pitchT = null;      // focus targets (null = free spin)
var SPH_R = 300, PERSP = 900, CY0 = 0;
function reheat() { temp = 1; }
resize();

function baseNode(id, x, y) {
  return { id, x, y, vx: 0, vy: 0, baseR: 26, r: 26 * SCALE, glow: 0, img: null, seed: 1 };
}

function setRadius(n, baseR) {
  n.baseR = baseR;
  n.r = baseR * SCALE;
}

/* ---------- living gallery: every neuron wears a photo ----------
   Each category node displays a real photo from its category, swapped for
   a fresh one every ~5 minutes (staggered so the wall breathes rather
   than blinks), with a 1s crossfade. */
const SWAP_MS = 5 * 60 * 1000;

// Frame cap in lite mode. ?fps=N overrides (60 = uncapped in practice).
const FRAME_MS = (() => {
  const q = new URLSearchParams(location.search).get("fps");
  const n = q !== null ? parseInt(q, 10) : 0;
  return n > 0 ? Math.floor(1000 / n) : 30;
})();

// How many pathways a weak device draws. ?edges=N overrides for measuring.
const EDGE_CAP = (() => {
  const q = new URLSearchParams(location.search).get("edges");
  return q !== null ? parseInt(q, 10) : 70;
})();

/* Round photo sprites.

   Drawing a face used to mean an arc clip plus a downscale of the full 512px
   thumb, per node, per frame — twice mid-crossfade. On a budget Android-TV box
   that pinned the whole scene at ~2fps. Rasterise each photo into a small round
   sprite once instead, and let frames just blit it, scaled.

   The size is FIXED, not derived from the on-screen radius: every node's radius
   changes every frame as the sphere turns (pscale is the perspective factor),
   so a size-derived sprite would re-rasterise constantly — which is exactly the
   cost we're trying to avoid. One sprite per photo, for its whole life. */
const SPRITE_PX = 160;
function roundSprite(im) {
  if (!im.naturalWidth) return null;
  if (im._rsCanvas) return im._rsCanvas;
  const size = SPRITE_PX;
  const cv = document.createElement("canvas");
  cv.width = cv.height = size;
  const c = cv.getContext("2d");
  c.beginPath();
  c.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2);
  c.clip();
  const iw = im.naturalWidth, ih = im.naturalHeight, sq = Math.min(iw, ih);
  c.drawImage(im, (iw - sq) / 2, (ih - sq) / 2, sq, sq, 0, 0, size, size);
  im._rsCanvas = cv;
  return cv;
}

function assignPhoto(n) {
  const url = n.core
    ? "/api/catphoto?family=1"
    : `/api/catphoto?dim=${encodeURIComponent(n.dim)}&value=${encodeURIComponent(n.value)}`;
  fetch(url).then((r) => r.ok ? r.json() : null).then((d) => {
    if (!d || !d.thumb) return;
    const img = new Image();
    img.onload = () => {
      n.imgNext = img;
      n.imgFade = 0;          // crossfade ramps in draw
      n.photoId = d.id;
    };
    img.src = d.thumb;
  }).catch(() => {});
  n.nextSwap = performance.now() + SWAP_MS * (0.75 + Math.random() * 0.5);
}

setInterval(() => {
  if (view !== "categories") return;
  const now = performance.now();
  for (const n of nodes.values())
    if (n.nextSwap && now > n.nextSwap) assignPhoto(n);
}, 20000);

/* ---------- category view ----------
   Deterministic phyllotaxis layout: biggest categories at the center,
   smaller ones spiraling outward on a sunflower pattern scaled to the
   viewport — centered and on-screen BY CONSTRUCTION, no physics to drift.
   Life comes from a gentle sway around each neuron's home position. */
function layoutCategories() {
  const ns = [...nodes.values()];
  if (!ns.length) return;
  const nsAll = ns.filter((n) => !n.core);
  ns.length = 0; ns.push(...nsAll);
  // cluster by dimension (community-graph reference): contiguous
  // Fibonacci-sphere runs put same-hued categories in neighborhoods, so
  // inter-community bundles emerge naturally
  ns.sort((a, b) => (a.dim || "").localeCompare(b.dim || "")
                    || b.count - a.count);
  CY0 = (52 + H) / 2;
  SPH_R = Math.min(W / 2 - 46, (H - 52) / 2 - 64) * 0.86;
  PERSP = SPH_R * 2.05;   // strong perspective: front stars swell ~2x, back recede ~0.65x
  const golden = 2.39996323;
  ns.forEach((n, i) => {                 // Fibonacci sphere: uniform spread
    const t = (i + 0.5) / ns.length;
    const sy = 1 - 2 * t;
    const sr = Math.sqrt(Math.max(0, 1 - sy * sy));
    const th = i * golden;
    n.p3 = [Math.cos(th) * sr * SPH_R, sy * SPH_R, Math.sin(th) * sr * SPH_R];
    n.x = W / 2; n.y = CY0; n.pscale = 1; n.depth = 0;
  });
  const spacing = SPH_R * 1.8 / Math.sqrt(ns.length);
  for (const n of ns)
    if (n.r > spacing * 0.7) setRadius(n, (spacing * 0.7) / SCALE);
}

async function loadCategories() {
  // fewer neurons on small screens so the web has room to breathe, and fewer
  // again on weak devices — edge count grows with the square of node count,
  // and edges are what cost. A TV is also viewed from across a room, where 40
  // labelled neurons is unreadable anyway.
  const top = LITE ? 24 : (Math.min(W, H) < 700 ? 22 : 40);
  const res = await fetch(`/api/categories?top=${top}`);
  if (!res.ok) throw new Error("busy");
  const data = await res.json();
  nodes.clear();
  const maxC = Math.max(1, ...data.nodes.map((n) => n.count));
  for (const c of data.nodes) {
    const seed = hashStr(c.key);
    const n = {
      ...baseNode(c.key, W / 2, H / 2),
      ...c,
      seed,
      label: c.value,
    };
    setRadius(n, 14 + 26 * Math.sqrt(c.count / maxC));
    nodes.set(c.key, n);
  }
  layoutCategories();
  // Only the biggest neurons wear photos. On weak boxes each photo face costs
  // real milliseconds per frame, and a thumbnail on a tiny distant star reads
  // as a smudge anyway — so spend the budget where it's actually visible.
  // ?photos=N overrides (0 = none) for measuring a new device.
  const capParam = new URLSearchParams(location.search).get("photos");
  const cap = capParam !== null ? parseInt(capParam, 10)
                                : (LITE ? 10 : nodes.size);
  const bySize = [...nodes.values()].sort((a, b) => (b.count || 0) - (a.count || 0));
  bySize.forEach((n, i) => { if (i < cap) assignPhoto(n); });
  edges = data.edges.filter((e) => nodes.has(e.a) && nodes.has(e.b));
  // the nucleus: our family, at the exact center of the sphere — the fixed
  // point every memory orbits
  const core = {
    ...baseNode("__family__", W / 2, CY0),
    dim: "family", value: "Our Family", label: "Our Family",
    count: data.nodes.reduce((m, c) => Math.max(m, c.count), 0),
    seed: hashStr("__family__"), core: true,
    p3: [0, 0, 0], pscale: 1, depth: 0,
  };
  setRadius(core, 34);
  nodes.set(core.id, core);
  assignPhoto(core);
  for (const [key, n] of nodes) {
    if (n.core) continue;
    edges.push({ a: "__family__", b: key, core: true,
                 weight: n.count,
                 strands: n.dim === "people" ? 5 : 1 });
  }
  focusId = null;
  focusNbr = null;
  yawT = null;
  edgeLayerValid = false;
  reheat();
}

async function openCategory(key) {
  const n = nodes.get(key);
  if (!n) return;
  if (n.core) { openFamily(n); return; }
  focusId = key;
  n.glow = 1;
  // rotate the sphere so this neuron faces the viewer
  const [bx, by, bz] = n.p3;
  const h = Math.hypot(bx, bz) || 0.0001;
  yawT = Math.atan2(-bx, bz) + Math.PI;
  pitchT = Math.max(-1.3, Math.min(1.3, Math.atan2(-by, h)));
  focusNbr = new Set([key]);
  zoomT = Math.min(3.5, Math.max(zoomT, 1.35));
  for (const e of edges) {
    if (e.a === key) focusNbr.add(e.b);
    if (e.b === key) focusNbr.add(e.a);
  }
  reheat();
  fireRing = { x: n.x, y: n.y, t: 0 };
  if (ambient) return;
  let d;
  try {
    const res = await fetch(
      `/api/category?dim=${encodeURIComponent(n.dim)}&value=${encodeURIComponent(n.value)}`);
    if (!res.ok) return;
    d = await res.json();
  } catch (e) { return; }
  document.getElementById("catTitle").textContent = `${d.value}`;
  const meta = document.getElementById("catMeta");
  meta.innerHTML = `${d.count} memor${d.count === 1 ? "y" : "ies"} · ` +
    `${d.dim.replace(/_/g, " ")} · ` +
    `<a href="/gallery?search=${encodeURIComponent(d.value)}" ` +
    `style="color:#8fd4ff">view all ${d.count} in gallery →</a>`;
  fillGrid(d.photos);
  panelPager = { dim: n.dim, value: n.value, offset: d.photos.length,
                 total: d.count };
  updateMoreBtn();
  // connected categories: the lit strands, made tappable — tap a chip to
  // see the SHARED photos behind that connection
  const conn = document.getElementById("catConn");
  conn.innerHTML = "";
  const nbrs = edges
    .filter((e) => e.a === key || e.b === key)
    .map((e) => ({ id: e.a === key ? e.b : e.a, w: e.weight || 1 }))
    .sort((x, y) => y.w - x.w);
  for (const nb of nbrs) {
    const other = nodes.get(nb.id);
    if (!other) continue;
    const chip = document.createElement("b");
    chip.textContent = `${other.label} · ${nb.w}`;
    chip.style.borderColor = dimColor(other.dim);
    chip.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      try {
        const r = await fetch(`/api/intersect?d1=${encodeURIComponent(n.dim)}` +
          `&v1=${encodeURIComponent(n.value)}&d2=${encodeURIComponent(other.dim)}` +
          `&v2=${encodeURIComponent(other.value)}`);
        if (!r.ok) return;
        const ix = await r.json();
        document.getElementById("catTitle").textContent =
          `${n.value} × ${other.value}`;
        document.getElementById("catMeta").textContent =
          `${ix.count} shared memor${ix.count === 1 ? "y" : "ies"} — tap ${other.label} in the web to move there`;
        fillGrid(ix.photos);
        panelPager = null; updateMoreBtn();
      } catch (e) {}
    });
    conn.appendChild(chip);
  }
  document.getElementById("panel").classList.add("hidden");
  document.getElementById("catPanel").classList.remove("hidden");
}

async function openFamily(core) {
  focusId = core.id;
  core.glow = 1;
  focusNbr = new Set([core.id]);
  for (const [key, n] of nodes) if (n.dim === "people") focusNbr.add(key);
  if (ambient) return;
  let d;
  try {
    const res = await fetch("/api/family");
    if (!res.ok) return;
    d = await res.json();
  } catch (e) { return; }
  document.getElementById("catTitle").textContent = "Our Family";
  document.getElementById("catMeta").textContent =
    `${d.total.toLocaleString()} memories · ${d.from || "?"}–${d.to || "?"} · us, captured in photographs`;
  const conn = document.getElementById("catConn");
  conn.innerHTML = "";
  for (const person of d.people) {
    const chip = document.createElement("b");
    chip.textContent = `${person.name} · ${person.count}`;
    chip.style.borderColor = FAMILY_GOLD;
    chip.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const key = `people:${person.name}`;
      if (nodes.has(key)) openCategory(key);
    });
    conn.appendChild(chip);
  }
  fillGrid(d.photos);
  panelPager = null; updateMoreBtn();
  document.getElementById("panel").classList.add("hidden");
  document.getElementById("catPanel").classList.remove("hidden");
}

var panelPager = null;

function fillGrid(photos, append) {
  const grid = document.getElementById("catGrid");
  if (!append) grid.innerHTML = "";
  for (const p of photos) {
    const img = document.createElement("img");
    img.src = p.thumb;
    img.loading = "lazy";
    img.addEventListener("click", () => {
      openLightbox(p.id);
      enterPhotoView(p.id);
    });
    grid.appendChild(img);
  }
}

function updateMoreBtn() {
  const btn = document.getElementById("catMore");
  if (!panelPager || panelPager.offset >= panelPager.total) {
    btn.style.display = "none";
    return;
  }
  btn.style.display = "block";
  btn.textContent =
    `load more (${panelPager.total - panelPager.offset} remaining)`;
}

document.getElementById("catMore").addEventListener("click", async () => {
  if (!panelPager) return;
  try {
    const r = await fetch(
      `/api/category?dim=${encodeURIComponent(panelPager.dim)}` +
      `&value=${encodeURIComponent(panelPager.value)}` +
      `&offset=${panelPager.offset}`);
    if (!r.ok) return;
    const d = await r.json();
    fillGrid(d.photos, true);
    panelPager.offset += d.photos.length;
    updateMoreBtn();
  } catch (e) {}
});

/* ---------- photo view (pathway explore) ---------- */
async function enterPhotoView(id) {
  view = "photos";
  document.body.classList.add("photoview");
  document.getElementById("catPanel").classList.add("hidden");
  nodes.clear(); edges = []; focusId = null;
  await focusPhoto(id);
}

function exitPhotoView() {
  view = "categories";
  document.body.classList.remove("photoview");
  document.getElementById("panel").classList.add("hidden");
  nodes.clear(); edges = []; focusId = null;
  loadCategories().catch(() => {});
}

function mkPhotoNode(n) {
  if (nodes.has(n.id)) return nodes.get(n.id);
  const angle = Math.random() * Math.PI * 2;
  const dist = (180 + Math.random() * 160) * SCALE;
  const cx = focusId != null && nodes.has(focusId) ? nodes.get(focusId).x : W / 2;
  const cy = focusId != null && nodes.has(focusId) ? nodes.get(focusId).y : H / 2;
  const node = {
    ...baseNode(n.id, cx + Math.cos(angle) * dist, cy + Math.sin(angle) * dist),
    ...n, seed: hashStr(String(n.id)),
  };
  const img = new Image();
  img.onload = () => { node.img = img; };
  img.src = n.thumb;
  nodes.set(n.id, node);
  return node;
}

async function focusPhoto(id) {
  let data;
  try {
    const res = await fetch(`/api/neighborhood?id=${id}`);
    if (!res.ok) return;
    data = await res.json();
  } catch (e) { return; }
  if (data.focus == null) return;
  focusId = data.focus;

  const keep = new Set(data.nodes.map((n) => n.id));
  for (const nid of [...nodes.keys()]) if (!keep.has(nid)) nodes.delete(nid);
  data.nodes.forEach(mkPhotoNode);
  edges = data.edges.filter((e) => nodes.has(e.a) && nodes.has(e.b));

  const f = nodes.get(focusId);
  setRadius(f, 44);
  reheat();
  fireRing = { x: f.x, y: f.y, t: 0 };
  for (const n of nodes.values()) if (n.id !== focusId) setRadius(n, 26);

  if (!ambient) showPanel(focusId);
}

/* ---------- full-screen lightbox ---------- */
let lbCurrentId = null;
async function openLightbox(id) {
  lbCurrentId = id;
  let d;
  try {
    const res = await fetch(`/api/photo?id=${id}`);
    if (!res.ok) return;
    d = await res.json();
  } catch (e) { return; }
  const img = document.getElementById("lbImg");
  img.src = d.thumb;                       // instant, low-res
  img.src = d.display || d.thumb;          // then the 1600px rendition
  document.getElementById("lbDate").textContent = d.taken_at
    ? new Date(d.taken_at).toDateString() : "date unknown";
  const tags = document.getElementById("lbTags");
  tags.innerHTML = "";
  for (const values of Object.values(d.tags || {}))
    for (const v of values) {
      const b = document.createElement("b");
      b.textContent = v;
      tags.appendChild(b);
    }
  document.getElementById("lightbox").classList.remove("hidden");
}

function closeLightbox() {
  document.getElementById("lightbox").classList.add("hidden");
}
document.getElementById("lightbox").addEventListener("click", closeLightbox);
document.getElementById("lbClose").addEventListener("click", closeLightbox);
document.getElementById("lbRemove").addEventListener("click", async (e) => {
  e.stopPropagation();
  if (lbCurrentId == null) return;
  if (!confirm("Remove this photo from the display? (Moves to the restorable Removed bin — nothing is deleted.)")) return;
  try { await fetch(`/api/photo/remove?id=${lbCurrentId}`); } catch (err) {}
  lbCurrentId = null;
  closeLightbox();
});
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeLightbox();
});

async function showPanel(id) {
  let d;
  try {
    const res = await fetch(`/api/photo?id=${id}`);
    if (!res.ok) return;
    d = await res.json();
  } catch (e) { return; }
  document.getElementById("panelImg").src = d.thumb;
  document.getElementById("panelDate").textContent = d.taken_at
    ? new Date(d.taken_at).toDateString() : "date unknown";
  const tags = document.getElementById("panelTags");
  tags.innerHTML = "";
  for (const [dim, values] of Object.entries(d.tags || {}))
    for (const v of values) {
      const b = document.createElement("b");
      b.textContent = v;
      tags.appendChild(b);
    }
  const why = Object.entries(d.relations || {})
    .map(([rel, n]) => `${n} ${REL_LABELS[rel] || rel}`)
    .join(" · ");
  document.getElementById("panelWhy").textContent =
    why ? `pathways: ${why}` : "no pathways yet";
  document.getElementById("panel").classList.remove("hidden");
}

/* ---------- physics ---------- */
function step() {
  const ns = [...nodes.values()];

  if (view === "categories") {
    // orbit integration: ease to focus target, else inertial free spin
    if (yawT !== null) {
      let dy = yawT - yaw; dy = Math.atan2(Math.sin(dy), Math.cos(dy));
      yaw += dy * 0.08;
      pitch += (pitchT - pitch) * 0.08;
      if (Math.abs(dy) < 0.002 && Math.abs(pitchT - pitch) < 0.002) { yawT = null; }
    } else {
      yaw += yawV; pitch += pitchV;
      // idle: slow drift on all three axes — a museum turntable, not a top
      yawV = yawV * 0.97 + 0.0012 * 0.03 * (dragging ? 0 : 1);
      pitchV = pitchV * 0.94 +
        (dragging ? 0 : Math.sin(tick * 0.0011) * 0.00035 * 0.06);
      if (!dragging && focusId == null) roll += 0.00055;
      else roll *= 0.96;                    // ease flat when interacting
    }
    pitch = Math.max(-1.35, Math.min(1.35, pitch));
    zoom += (zoomT - zoom) * 0.12;
    const cY = Math.cos(yaw), sY = Math.sin(yaw);
    const cP = Math.cos(pitch), sP = Math.sin(pitch);
    for (const n of ns) {
      if (n.core) {
        n.x = W / 2; n.y = CY0; n.pscale = 1; n.depth = 0;
        n.glow = Math.max(0, n.glow - 0.012);
        continue;
      }
      const [x, y, z] = n.p3;
      const x1 = x * cY + z * sY, z1 = -x * sY + z * cY;
      const y1 = y * cP - z1 * sP, z2 = y * sP + z1 * cP;
      const pf = (PERSP / (PERSP + z2)) * zoom;
      const rx = x1 * pf, ry = y1 * pf;
      const cR = Math.cos(roll), sR = Math.sin(roll);
      n.x = W / 2 + rx * cR - ry * sR;
      n.y = CY0 + rx * sR + ry * cR;
      n.pscale = pf;
      n.depth = z2 / SPH_R;              // -1 front .. +1 back
      n.glow = Math.max(0, n.glow - 0.012);
    }
    return;
  }

  const f = focusId != null ? nodes.get(focusId) : null;

  for (let i = 0; i < ns.length; i++) {
    const a = ns[i];
    if (f && a.id === focusId && view === "photos") {
      a.vx += (W / 2 - a.x) * 0.02;
      a.vy += (H / 2 - a.y) * 0.02;
    } else if (f && view === "photos") {
      a.vx += (f.x - a.x) * 0.0006;
      a.vy += (f.y - a.y) * 0.0006;
    } else {
      // categories drift toward the middle; big neurons feel more gravity so
      // high-count hubs sit central instead of pinning to the walls
      const g = 0.0004 + 0.0009 * (a.r / 40);
      a.vx += (W / 2 - a.x) * g;
      a.vy += (H / 2 - a.y) * g;
    }
    for (let j = i + 1; j < ns.length; j++) {
      const b = ns[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      let d2 = dx * dx + dy * dy || 1;
      if (d2 < 90000 * SCALE * SCALE) {
        const force = ((view === "categories" ? 2200 : 2600) * SCALE * SCALE) / d2;
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        a.vx += dx * force; a.vy += dy * force;
        b.vx -= dx * force; b.vy -= dy * force;
      }
    }
  }
  for (const e of edges) {
    const a = nodes.get(e.a), b = nodes.get(e.b);
    if (!a || !b) continue;
    const rest = (a.r + b.r) * 2.2 + 40 * SCALE;
    let dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 1;
    // linear spring — the old force grew with d^2 and slingshotted the web
    const f = (d - rest) * 0.003 * (0.5 + (e.weight || 1) / 2);
    dx /= d; dy /= d;
    a.vx += dx * f; a.vy += dy * f;
    b.vx -= dx * f; b.vy -= dy * f;
  }
  temp = Math.max(0.02, temp * 0.994);
  const vmax = 1 + 5 * SCALE;
  for (const n of ns) {
    n.vx *= 0.82; n.vy *= 0.82;
    const sp = Math.hypot(n.vx, n.vy);
    if (sp > vmax) { n.vx *= vmax / sp; n.vy *= vmax / sp; }
    n.x += n.vx * temp; n.y += n.vy * temp;
    const mx = 26 + 24 * SCALE, myt = 46 + 24 * SCALE, myb = 56 + 24 * SCALE;
    if (n.x < mx) { n.x = mx; if (n.vx < 0) n.vx = 0; }
    if (n.x > W - mx) { n.x = W - mx; if (n.vx > 0) n.vx = 0; }
    if (n.y < myt) { n.y = myt; if (n.vy < 0) n.vy = 0; }
    if (n.y > H - myb) { n.y = H - myb; if (n.vy > 0) n.vy = 0; }
    n.glow = Math.max(0, n.glow - 0.012);
  }

  if (!ns.length) return;
  // Deterministic fit: cooling can freeze the web wherever it drifted, so
  // rigid-translate the centroid to screen center every frame (no energy
  // added) and, if the cluster outgrows the viewport, squeeze it gently.
  let sx = 0, sy = 0;
  for (const n of ns) { sx += n.x; sy += n.y; }
  const shiftX = (W / 2 - sx / ns.length) * 0.05;
  const shiftY = (H / 2 - sy / ns.length) * 0.05;
  let maxRx = 1, maxRy = 1;
  for (const n of ns) {
    n.x += shiftX; n.y += shiftY;
    maxRx = Math.max(maxRx, Math.abs(n.x - W / 2));
    maxRy = Math.max(maxRy, Math.abs(n.y - H / 2));
  }
  const fit = Math.min(1, (W / 2 - 60) / maxRx, (H / 2 - 80) / maxRy);
  if (fit < 1) {
    const k = 0.985 + 0.015 * fit; // ease toward fitting, never snaps
    for (const n of ns) {
      n.x = W / 2 + (n.x - W / 2) * k;
      n.y = H / 2 + (n.y - H / 2) * k;
    }
  }
}

function drawFamilyCore(n, isFocus) {
  const r = n.r;
  const breathe = 1 + 0.05 * Math.sin(tick * 0.015);
  ctx.save();
  // corona
  const g1 = ctx.createRadialGradient(n.x, n.y, r * 0.2, n.x, n.y, r * 3.1 * breathe);
  g1.addColorStop(0, "rgba(255,233,184,0.5)");
  g1.addColorStop(0.4, "rgba(255,210,122,0.14)");
  g1.addColorStop(1, "transparent");
  ctx.fillStyle = g1;
  ctx.beginPath();
  ctx.arc(n.x, n.y, r * 3.1 * breathe, 0, Math.PI * 2);
  ctx.fill();
  // nucleus
  const g2 = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, r * breathe);
  g2.addColorStop(0, "#fffdf4");
  g2.addColorStop(0.55, "#ffe9b8");
  g2.addColorStop(1, "#d8a94f");
  ctx.fillStyle = g2;
  ctx.globalAlpha = 0.96;
  ctx.beginPath();
  ctx.arc(n.x, n.y, r * breathe, 0, Math.PI * 2);
  ctx.fill();
  // orbit ring
  ctx.strokeStyle = "#ffe9b8";
  ctx.globalAlpha = 0.22 + (isFocus ? 0.25 : 0);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.ellipse(n.x, n.y, r * 2.1, r * 0.75, tick * 0.001, 0, Math.PI * 2);
  ctx.stroke();
  // the family photo inside the nucleus, warm-ringed
  if (n.imgNext && n.imgFade !== undefined && n.imgFade < 1) {
    n.imgFade = Math.min(1, (n.imgFade || 0) + 0.02);
    if (n.imgFade >= 1) { n.img = n.imgNext; n.imgNext = null; }
  }
  const rr = r * breathe * 0.9;
  const drawFace = (im, alpha) => {
    if (!im) return;
    const sp = roundSprite(im);
    if (!sp) return;
    ctx.globalAlpha = alpha;
    ctx.drawImage(sp, n.x - rr, n.y - rr, rr * 2, rr * 2);
  };
  drawFace(n.img, 0.94);
  if (n.imgNext) drawFace(n.imgNext, 0.94 * n.imgFade);
  if (n.img || n.imgNext) {
    ctx.globalAlpha = 0.9;
    ctx.strokeStyle = FAMILY_GOLD;
    ctx.lineWidth = Math.max(1.5, rr * 0.06);
    ctx.beginPath();
    ctx.arc(n.x, n.y, rr, 0, Math.PI * 2);
    ctx.stroke();
  }

  // name in serif — this one isn't a category, it's us
  ctx.globalAlpha = 0.92;
  ctx.fillStyle = "#fff3d8";
  ctx.font = `600 ${Math.max(13, r * 0.55)}px Georgia, serif`;
  ctx.textAlign = "center";
  ctx.fillText("Our Family", n.x, n.y + r * 2.3 + 16);
  ctx.restore();
}

/* ---------- filament web (baked) + flow particles ----------
   The inspiration set (Benzi bloom, flight patterns, Blindsight) is
   thousands of hair-thin additive strands. The phyllotaxis layout is
   static, so the whole web bakes into ONE offscreen layer per layout —
   the frame loop just blits it. Living motion comes from light particles
   riding the strands and a breathing focus overlay. */
var edgeLayer = document.createElement("canvas");
var edgeLayerValid = false;
const flows = [];               // particles riding edges: {e, t, speed, col}
const FLOW_N = 140;

function strand3D(a, b, rand) {
  // endpoints jittered on each node's sphere-point, control bulged outward
  const j = (v) => v + (rand() - 0.5) * 34 * SCALE;
  const p1 = [j(a.p3[0]), j(a.p3[1]), j(a.p3[2])];
  const p2 = [j(b.p3[0]), j(b.p3[1]), j(b.p3[2])];
  const mx = (p1[0] + p2[0]) / 2, my = (p1[1] + p2[1]) / 2, mz = (p1[2] + p2[2]) / 2;
  const ml = Math.hypot(mx, my, mz) || 1;
  const inner = Math.hypot(...a.p3) < 1 || Math.hypot(...b.p3) < 1;
  const bulge = inner ? ml * (1.06 + rand() * 0.25)
                      : SPH_R * (1.02 + rand() * 0.3);
  return { p1, p2, c: [mx / ml * bulge, my / ml * bulge, mz / ml * bulge] };
}

function proj(pt, cY, sY, cP, sP) {
  const x1 = pt[0] * cY + pt[2] * sY, z1 = -pt[0] * sY + pt[2] * cY;
  const y1 = pt[1] * cP - z1 * sP, z2 = pt[1] * sP + z1 * cP;
  const pf = (PERSP / (PERSP + z2)) * zoom;
  const rx = x1 * pf, ry = y1 * pf;
  const cR = Math.cos(roll), sR = Math.sin(roll);
  return [W / 2 + rx * cR - ry * sR, CY0 + rx * sR + ry * cR, z2 / SPH_R];
}

function strandGeometry(a, b, si, rand) {
  // seeded per-strand: scattered endpoints inside each soma, control point
  // fanned out perpendicular so bundles spread mid-flight and converge at
  // the nodes — the flight-path look
  const a1 = rand() * Math.PI * 2, a2 = rand() * Math.PI * 2;
  const x1 = a.hx + Math.cos(a1) * a.r * rand() * 0.9;
  const y1 = a.hy + Math.sin(a1) * a.r * rand() * 0.9;
  const x2 = b.hx + Math.cos(a2) * b.r * rand() * 0.9;
  const y2 = b.hy + Math.sin(a2) * b.r * rand() * 0.9;
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
  const dx = x2 - x1, dy = y2 - y1;
  const d = Math.hypot(dx, dy) || 1;
  const spread = (rand() - 0.5) * Math.min(d * 0.55, 150 * SCALE);
  const cx = mx - (dy / d) * spread;
  const cy = my + (dx / d) * spread;
  return { x1, y1, cx, cy, x2, y2 };
}

function buildEdgeLayer() {
  // 3D: strands + spines built once in sphere space, projected live
  if (view !== "categories") return;
  flows.length = 0;
  const maxW = Math.max(1, ...edges.filter((e) => !e.core)
                                   .map((e) => e.weight || 1));
  for (const e of edges) {
    const a = nodes.get(e.a), b = nodes.get(e.b);
    if (!a || !b || !a.p3 || !b.p3) continue;
    const w = e.core ? 0.22 : (e.weight || 1) / maxW;
    e.w = w;
    // spine: the direct neural pathway node-to-node, gentle outward bow
    const k = e.core ? 1.05 : 1.16;
    e.spine = {
      p1: [...a.p3], p2: [...b.p3],
      c: [(a.p3[0] + b.p3[0]) / 2 * k, (a.p3[1] + b.p3[1]) / 2 * k,
          (a.p3[2] + b.p3[2]) / 2 * k],
    };
    // filament texture around the spine — the single most expensive thing on
    // screen. 215 edges x up to 8 additive quadratic strokes is ~1,300 strokes
    // a frame, which is what pins TV boxes at single-digit fps. Weak devices
    // get the spine only.
    const strands = LITE ? 0 : (e.strands || (2 + Math.round(w * 6)));
    const rand = rng(hashStr(e.a + "|" + e.b));
    e.geo3 = [];
    for (let si = 0; si < strands; si++) e.geo3.push(strand3D(a, b, rand));
  }
  // ...and only the strongest pathways are drawn at all on weak devices.
  const ranked = [...edges].sort((x, y) => (y.w || 0) - (x.w || 0));
  ranked.forEach((e, i) => { e.rank = i; });
  const withGeo = edges.filter((e) => e.geo3 && e.geo3.length);
  for (let i = 0; i < FLOW_N && withGeo.length; i++) {
    const e = withGeo[Math.floor(Math.random() * withGeo.length)];
    const g3 = e.geo3[Math.floor(Math.random() * e.geo3.length)];
    const a = nodes.get(e.a);
    flows.push({ g3, t: Math.random(), speed: 0.0012 + Math.random() * 0.003,
                 col: Math.random() > 0.5 ? dimColor(a.dim) : "#dff4ff" });
  }
  edgeLayerValid = true;
}

function bezPoint(s, t) {
  const u = 1 - t;
  return [u * u * s.x1 + 2 * u * t * s.cx + t * t * s.x2,
          u * u * s.y1 + 2 * u * t * s.cy + t * t * s.y2];
}

function drawFlows() {
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (const f of flows) {
    f.t += f.speed;
    if (f.t > 1) f.t = 0;
    if (!f.g3 || !f.g3.s2) continue;
    const [x, y] = bezPoint(f.g3.s2, f.t);
    const s2 = 0.8 * Math.sin(f.t * Math.PI);
    ctx.globalAlpha = s2;
    ctx.fillStyle = f.col;
    ctx.beginPath();
    ctx.arc(x, y, 1.7, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = s2 * 0.3;
    ctx.beginPath();
    ctx.arc(x, y, 3.6, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

/* ---------- biological rendering ----------
   Per-frame shadowBlur is what made phones chop: every blur is a gaussian
   pass over the fill area. Instead each neuron (dendrites + glow + soma) is
   painted ONCE into an offscreen sprite — the blur cost happens at build
   time — and frames just drawImage it. Life comes from cheap per-frame
   alpha pulsing + twinkle dots, no shadows anywhere in the frame loop. */
var SPRITE_SS = 2;   // raised to match DPR in resize()

function buildSprite(n) {
  const reach = n.r * 3.9 + 24;
  const size = Math.ceil(reach * 2 * SPRITE_SS);
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const s = c.getContext("2d");
  s.scale(SPRITE_SS, SPRITE_SS);
  const cx = reach, cy = reach;
  const rand = rng(n.seed);
  const nb = 8 + Math.floor(rand() * 4);   // finer, denser dendrites
  const tips = [];
  const tint = dimColor(n.dim);

  // orbital ring system on hub neurons (Blindsight disc)
  if (n.r > 16 * SCALE + 4) {
    s.save();
    s.strokeStyle = tint;
    for (let ri = 0; ri < 2; ri++) {
      s.globalAlpha = 0.14 - ri * 0.05;
      s.lineWidth = 0.7;
      s.beginPath();
      s.ellipse(cx, cy, n.r * (1.9 + ri * 0.5), n.r * (0.62 + ri * 0.18),
                rand() * Math.PI, 0, Math.PI * 2);
      s.stroke();
    }
    // ring dust
    s.fillStyle = tint;
    for (let di = 0; di < 26; di++) {
      const ang = rand() * Math.PI * 2;
      s.globalAlpha = 0.1 + rand() * 0.3;
      s.fillRect(cx + Math.cos(ang) * n.r * (1.8 + rand() * 0.7),
                 cy + Math.sin(ang) * n.r * (0.55 + rand() * 0.25), 1, 1);
    }
    s.restore();
  }

  s.strokeStyle = BLUE;
  s.shadowColor = BLUE;
  for (let i = 0; i < nb; i++) {
    const baseAng = (i / nb) * Math.PI * 2 + rand() * 0.9;
    const len = n.r * (2.1 + rand() * 1.6);
    const x1 = cx + Math.cos(baseAng) * n.r * 0.85;
    const y1 = cy + Math.sin(baseAng) * n.r * 0.85;
    const cx1 = cx + Math.cos(baseAng + 0.35) * len * 0.55;
    const cy1 = cy + Math.sin(baseAng + 0.35) * len * 0.55;
    const x2 = cx + Math.cos(baseAng + (rand() - 0.5) * 0.5) * len;
    const y2 = cy + Math.sin(baseAng + (rand() - 0.5) * 0.5) * len;

    s.globalAlpha = 0.22;
    s.lineWidth = Math.max(0.5, n.r * 0.09);
    s.shadowBlur = 6;
    s.beginPath();
    s.moveTo(x1, y1);
    s.quadraticCurveTo(cx1, cy1, x2, y2);
    s.stroke();

    const bx = cx + Math.cos(baseAng + 0.2) * len * 0.6;
    const by = cy + Math.sin(baseAng + 0.2) * len * 0.6;
    const sAng = baseAng + (rand() > 0.5 ? 0.8 : -0.8);
    s.globalAlpha = 0.18;
    s.lineWidth = Math.max(0.4, n.r * 0.05);
    s.beginPath();
    s.moveTo(bx, by);
    s.quadraticCurveTo(
      bx + Math.cos(sAng) * len * 0.25, by + Math.sin(sAng) * len * 0.25,
      bx + Math.cos(sAng + 0.3) * len * 0.42, by + Math.sin(sAng + 0.3) * len * 0.42);
    s.stroke();

    s.globalAlpha = 0.45;
    s.shadowBlur = 8;
    s.fillStyle = "#bfe9ff";
    s.beginPath();
    s.arc(x2, y2, Math.max(1, n.r * 0.07), 0, Math.PI * 2);
    s.fill();
    tips.push({ dx: x2 - cx, dy: y2 - cy });
  }

  // soma with baked halo
  s.shadowColor = BLUE;
  s.shadowBlur = 26;
  const g = s.createRadialGradient(cx, cy, n.r * 0.1, cx, cy, n.r);
  g.addColorStop(0, "#eaffff");
  g.addColorStop(0.35, "#8adcff");
  g.addColorStop(0.8, tint);      // soma rim carries the dimension hue
  g.addColorStop(1, "#123a5f");
  s.fillStyle = g;
  s.globalAlpha = 1;
  s.beginPath();
  s.arc(cx, cy, n.r, 0, Math.PI * 2);
  s.fill();

  n.sprite = c;
  n.spriteReach = reach;
  n.tips = tips;
}

function drawCategoryNode(n) {
  const isFocus = n.id === focusId;
  if (n.core) { drawFamilyCore(n, isFocus); return; }
  if (!n.sprite) buildSprite(n);
  const lit = !focusNbr || focusNbr.has(n.id);
  const pulse = 0.9 + 0.1 * Math.sin(tick * 0.02 + (n.seed % 13));
  const nbrGlow = focusNbr && !isFocus && lit ? 0.12 * Math.sin(tick * 0.06 + n.seed % 5) : 0;
  const ps = n.pscale || 1;
  const dfade = 0.3 + 0.7 * (1 - ((n.depth || 0) + 1) / 2);
  const nodeAlpha = (lit ? 0.62 + 0.38 * (isFocus ? 1 : n.glow) + nbrGlow : 0.14)
    * pulse * dfade;
  ctx.globalAlpha = nodeAlpha;
  const half = n.spriteReach * ps;
  ctx.drawImage(n.sprite, n.x - half, n.y - half, half * 2, half * 2);
  // the photo face: cover-cropped into the soma circle, crossfading swaps
  if (n.imgNext && n.imgFade !== undefined && n.imgFade < 1) {
    n.imgFade = Math.min(1, (n.imgFade || 0) + 0.02);
    if (n.imgFade >= 1) { n.img = n.imgNext; n.imgNext = null; }
  }
  const rr = n.r * ps * 0.92;
  const drawFace = (im, alpha) => {
    if (!im || rr < 5) return;
    const sp = roundSprite(im);
    if (!sp) return;
    ctx.globalAlpha = alpha;
    ctx.drawImage(sp, n.x - rr, n.y - rr, rr * 2, rr * 2);
  };
  const photoAlpha = lit
    ? Math.min(1, 0.9 * dfade + 0.25)
    : 0.15;
  drawFace(n.img, photoAlpha);
  if (n.imgNext) drawFace(n.imgNext, photoAlpha * n.imgFade);
  if (rr >= 5 && (n.img || n.imgNext)) {
    ctx.globalAlpha = nodeAlpha;
    ctx.strokeStyle = dimColor(n.dim);
    ctx.lineWidth = Math.max(1, rr * 0.07);
    ctx.beginPath();
    ctx.arc(n.x, n.y, rr, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  // twinkling synapse tips: two cheap dots, no shadow
  const t1 = n.tips[tick % n.tips.length ? (n.seed + 1) % n.tips.length : 0];
  const tw = 0.5 + 0.5 * Math.sin(tick * 0.05 + (n.seed % 10));
  if (t1) {
    ctx.globalAlpha = 0.5 * tw;
    ctx.fillStyle = "#dff4ff";
    ctx.beginPath();
    ctx.arc(n.x + t1.dx * (n.pscale || 1), n.y + t1.dy * (n.pscale || 1),
            Math.max(1.2, n.r * (n.pscale || 1) * 0.08), 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  // Blindsight-style HUD tag: leader line + boxed monospace label
  const isF = n.id === focusId;
  if ((n.depth || 0) > 0.45 && !isF) return;   // back hemisphere: no label
  const litL = !focusNbr || focusNbr.has(n.id);
  const psl = n.pscale || 1;
  const ly = n.y + n.r * psl + (isF ? 24 : 20);
  ctx.font = `${isF ? 15 : 13}px ui-monospace, Menlo, monospace`;
  const text = `${n.label.toUpperCase()}  ${n.count}`;
  const labelW = ctx.measureText(text).width;
  ctx.globalAlpha = isF ? 1 : litL ? 0.85 : 0.15;
  ctx.fillStyle = isF ? "#103652cc" : "#0a1f32aa";
  ctx.fillRect(n.x - labelW / 2 - 6, ly - 14, labelW + 12, 19);
  ctx.fillStyle = isF ? "#eaffff" : "#cfe8fa";
  ctx.textAlign = "center";
  ctx.fillText(text, n.x, ly);
  ctx.globalAlpha = 1;
}

let haloSprite = null; // shared radial glow, tinted per draw via alpha
function getHalo() {
  if (haloSprite) return haloSprite;
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const s = c.getContext("2d");
  const g = s.createRadialGradient(64, 64, 18, 64, 64, 64);
  g.addColorStop(0, "#7d95e6");
  g.addColorStop(0.5, "#3d54a344");
  g.addColorStop(1, "transparent");
  s.fillStyle = g;
  s.fillRect(0, 0, 128, 128);
  haloSprite = c;
  return c;
}

function drawPhotoNode(n) {
  const isFocus = n.id === focusId;
  const hr = n.r * (isFocus ? 2.6 : 2.0 + n.glow);
  ctx.globalAlpha = isFocus ? 0.9 : 0.55 + n.glow * 0.4;
  ctx.drawImage(getHalo(), n.x - hr, n.y - hr, hr * 2, hr * 2);
  ctx.globalAlpha = 1;

  ctx.save();
  ctx.beginPath();
  ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
  ctx.clip();
  if (n.img) {
    // cover-crop: fill the circle from the image center, no squishing
    const iw = n.img.naturalWidth, ih = n.img.naturalHeight;
    const s = Math.min(iw, ih);
    ctx.drawImage(n.img, (iw - s) / 2, (ih - s) / 2, s, s,
      n.x - n.r, n.y - n.r, n.r * 2, n.r * 2);
  }
  ctx.restore();

  ctx.beginPath();
  ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
  ctx.strokeStyle = isFocus ? "#c3d1ff" : "#3c4568";
  ctx.lineWidth = isFocus ? 2.5 : 1.2;
  ctx.stroke();
}

function drawEdges() {
  if (view === "categories") {
    if (!edgeLayerValid) buildEdgeLayer();
    const cY = Math.cos(yaw), sY = Math.sin(yaw);
    const cP = Math.cos(pitch), sP = Math.sin(pitch);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.lineWidth = 0.6;
    for (const e of edges) {
      if (!e.geo3) continue;
      if (LITE && e.rank >= EDGE_CAP) continue;
      const onF = focusId != null && (e.a === focusId || e.b === focusId);
      const colA = dimColor(nodes.get(e.a)?.dim);
      const colB = dimColor(nodes.get(e.b)?.dim);
      // the pathway spine: one unmistakable neural line per connection —
      // wide soft under-glow + thin bright core (two passes, no shadowBlur)
      {
        const g3 = e.spine || e.geo3[0];
        const P1 = proj(g3.p1, cY, sY, cP, sP);
        const C = proj(g3.c, cY, sY, cP, sP);
        const P2 = proj(g3.p2, cY, sY, cP, sP);
        const depth = (P1[2] + P2[2]) / 2;
        const dfade = 0.85 - depth * 0.25;
        const w = e.w || 0.3;
        let base = onF ? 0.75 : (focusNbr ? 0.06 : 0.34 + 0.34 * w);
        base *= dfade;
        const pulse = 1 + 0.15 * Math.sin(tick * 0.03 + (e.weight || 0));
        ctx.strokeStyle = colA;
        ctx.globalAlpha = base * 0.4;
        ctx.lineWidth = (4 + 6 * w) * pulse;
        ctx.beginPath();
        ctx.moveTo(P1[0], P1[1]);
        ctx.quadraticCurveTo(C[0], C[1], P2[0], P2[1]);
        ctx.stroke();
        ctx.strokeStyle = onF ? "#eaffff" : colB;
        ctx.globalAlpha = base;
        ctx.lineWidth = (1.4 + 1.8 * w) * pulse;
        ctx.stroke();
      }
      for (let si = 0; si < e.geo3.length; si++) {
        const g3 = e.geo3[si];
        const P1 = proj(g3.p1, cY, sY, cP, sP);
        const C = proj(g3.c, cY, sY, cP, sP);
        const P2 = proj(g3.p2, cY, sY, cP, sP);
        g3.s2 = { x1: P1[0], y1: P1[1], cx: C[0], cy: C[1], x2: P2[0], y2: P2[1] };
        const depth = (P1[2] + P2[2]) / 2;        // -1 front .. 1 back
        const dfade = 0.65 - depth * 0.35;         // back strands recede
        let a;
        if (onF) a = (0.3 + 0.14 * Math.sin(tick * 0.05 + si)) * dfade;
        else if (focusNbr) a = 0.03 * dfade;
        else a = (0.09 + 0.12 * (e.w || 0.3)) * dfade;
        ctx.strokeStyle = onF && si % 2 ? "#dff4ff" : (si % 2 ? colB : colA);
        ctx.globalAlpha = Math.max(0.015, a);
        ctx.beginPath();
        ctx.moveTo(P1[0], P1[1]);
        ctx.quadraticCurveTo(C[0], C[1], P2[0], P2[1]);
        ctx.stroke();
      }
    }
    ctx.restore();
    // relationship labels: shared-photo counts sit mid-strand when focused
    if (focusId != null) {
      ctx.save();
      ctx.font = "9px ui-monospace, Menlo, monospace";
      ctx.textAlign = "center";
      for (const e of edges) {
        if (e.a !== focusId && e.b !== focusId) continue;
        if (!e.geo3 || !e.geo3[0]) continue;
        const C = proj(e.geo3[0].c, cY, sY, cP, sP);
        ctx.globalAlpha = 0.75;
        ctx.fillStyle = "#0a1f32cc";
        const t = `${e.weight}`;
        const tw2 = ctx.measureText(t).width;
        ctx.fillRect(C[0] - tw2 / 2 - 3, C[1] - 7, tw2 + 6, 12);
        ctx.fillStyle = "#bfe4ff";
        ctx.fillText(t, C[0], C[1] + 3);
      }
      ctx.restore();
    }
    drawFlows();
    return;
  }
  const maxW = Math.max(1, ...edges.map((e) => e.weight || 1));
  for (const e of edges) {
    const a = nodes.get(e.a), b = nodes.get(e.b);
    if (!a || !b) continue;
    const onFocus = e.a === focusId || e.b === focusId;
    const color = view === "photos" ? (REL_COLORS[e.relation] || "#666") : BLUE;
    const w = (e.weight || 1) / maxW;
    const alpha = view === "photos"
      ? (onFocus ? 0.85 : 0.22)
      : (onFocus ? 0.65 : 0.05 + 0.18 * w);
    const width = view === "photos"
      ? 1 + (e.weight || 1) * (onFocus ? 2.4 : 1.2)
      : 0.5 + 2.2 * w + (onFocus ? 1 : 0);
    const mx = (a.x + b.x) / 2 + (a.y - b.y) * 0.12;
    const my = (a.y + b.y) / 2 + (b.x - a.x) * 0.12;
    ctx.strokeStyle = color;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.quadraticCurveTo(mx, my, b.x, b.y);
    // glow without shadowBlur: one wide faint pass under the crisp pass
    ctx.globalAlpha = alpha * 0.35;
    ctx.lineWidth = width * 3;
    ctx.stroke();
    ctx.globalAlpha = alpha;
    ctx.lineWidth = width;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
}

function draw() {
  tick++;
  ctx.clearRect(0, 0, W, H);
  ctx.drawImage(bg, 0, 0, W, H);
  drawEdges();

  if (fireRing) {
    fireRing.t += 0.03;
    const f = focusId != null ? nodes.get(focusId) : null;
    if (f) { fireRing.x = f.x; fireRing.y = f.y; }
    ctx.save();
    ctx.strokeStyle = "#dff4ff";
    ctx.globalAlpha = Math.max(0, 0.5 - fireRing.t * 0.5);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(fireRing.x, fireRing.y, 44 + fireRing.t * 160, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
    if (fireRing.t > 1) fireRing = null;
  }

  if (view === "categories") {
    [...nodes.values()].sort((a, b) => b.depth - a.depth).forEach(drawCategoryNode);
  } else {
    for (const n of nodes.values()) drawPhotoNode(n);
  }
}

// frame pacing + auto-lite: if the device can't hold a decent frame rate for a
// sustained stretch, drop to LITE (1x resolution, 30fps cap). Photos stay —
// since they became round sprites they cost a blit, not a clip-and-downscale.
let _lastFrame = 0, _slowFrames = 0, _liteTripped = false;
function goLite() {
  if (_liteTripped) return;
  _liteTripped = true; LITE = true;
  resize();   // re-render at DPR 1
}
function loop(ts) {
  ts = ts || 0;
  const dt = ts - _lastFrame;
  if (LITE && dt < FRAME_MS) { requestAnimationFrame(loop); return; }  // fps cap
  if (!LITE && _lastFrame) {
    if (dt > 45) { if (++_slowFrames > 90) goLite(); }            // ~1.5s of <22fps
    else _slowFrames = Math.max(0, _slowFrames - 2);
  }
  _lastFrame = ts;
  step();
  draw();
  requestAnimationFrame(loop);
}

/* ---------- interaction ----------
   Explicit pointer-tap detection: mobile Chrome's pan/double-tap-zoom
   heuristics can swallow canvas click events entirely, so we recognize
   taps ourselves (short press, little movement) from pointer events. */
function handleTap(x, y) {
  let best = null, bestD2 = Infinity;
  for (const n of nodes.values()) {
    const dx = x - n.x, dy = y - n.y;
    const d2 = dx * dx + dy * dy;
    const ps = view === "categories" ? (n.pscale || 1) : 1;
    const hitR = view === "categories" ? Math.max(n.r * ps * 1.8, 26) : Math.max(n.r, 26);
    if (d2 <= hitR * hitR && d2 < bestD2) { best = n; bestD2 = d2; }
  }
  if (best) {
    if (view === "categories") {
      // a star's tap opens its own constellation page
      if (best.core) { location.href = "/node?family=1"; return; }
      location.href = `/node?dim=${encodeURIComponent(best.dim)}` +
        `&value=${encodeURIComponent(best.value)}`;
    } else {
      // full-screen first; the graph recenters underneath for when it closes
      openLightbox(best.id);
      focusPhoto(best.id);
    }
    return;
  }
  if (view === "categories") { focusId = null; focusNbr = null; yawT = null; yawV = 0.004; zoomT = 1; }
  document.getElementById("panel").classList.add("hidden");
  document.getElementById("catPanel").classList.add("hidden");
}

let pdown = null;
var dragging = false;
canvas.addEventListener("wheel", (e) => {
  if (view !== "categories") return;
  e.preventDefault();
  zoomT = Math.max(0.5, Math.min(3.5, zoomT * Math.exp(-e.deltaY * 0.0012)));
}, { passive: false });

const ptrs = new Map();
let pinchD = null;
canvas.addEventListener("pointerdown", (e) => {
  cancelKiosk();
  ptrs.set(e.pointerId, [e.clientX, e.clientY]);
  if (ptrs.size === 2) { pinchD = null; pdown = null; dragging = false; }
  else {
    pdown = { x: e.clientX, y: e.clientY, lx: e.clientX, ly: e.clientY,
              t: performance.now() };
    dragging = false;
  }
  canvas.setPointerCapture?.(e.pointerId);
});
canvas.addEventListener("pointermove", (e) => {
  if (ptrs.has(e.pointerId)) ptrs.set(e.pointerId, [e.clientX, e.clientY]);
  if (ptrs.size === 2 && view === "categories") {
    const [p1, p2] = [...ptrs.values()];
    const d = Math.hypot(p1[0] - p2[0], p1[1] - p2[1]);
    if (pinchD !== null && pinchD > 0)
      zoomT = Math.max(0.5, Math.min(3.5, zoomT * (d / pinchD)));
    pinchD = d;
    return;
  }
  if (!pdown || view !== "categories") return;
  const dx = e.clientX - pdown.lx, dy = e.clientY - pdown.ly;
  pdown.lx = e.clientX; pdown.ly = e.clientY;
  if (Math.hypot(e.clientX - pdown.x, e.clientY - pdown.y) > 8) {
    dragging = true;
    yawT = null;                       // manual control overrides focus glide
    yaw += dx * 0.006;
    pitch -= dy * 0.006;
    yawV = dx * 0.006 * 0.6;           // inertia seeds from last motion
    pitchV = -dy * 0.006 * 0.6;
  }
});
canvas.addEventListener("pointerup", (e) => {
  ptrs.delete(e.pointerId);
  if (ptrs.size < 2) pinchD = null;
  if (!pdown) return;
  const moved = Math.hypot(e.clientX - pdown.x, e.clientY - pdown.y);
  const held = performance.now() - pdown.t;
  pdown = null;
  const wasDrag = dragging;
  dragging = false;
  if (!wasDrag && moved <= 14 && held <= 700) handleTap(e.clientX, e.clientY);
});
canvas.addEventListener("pointercancel", (e) => { ptrs.delete(e.pointerId); pinchD = null; pdown = null; });

document.getElementById("backBtn").addEventListener("click", exitPhotoView);

/* ---------- ambient: the brain fires on its own ---------- */
let ambientTimer = null;

function setAmbient(on) {
  ambient = on;
  document.body.classList.toggle("ambient", on);
  document.getElementById("modeBtn").textContent = on ? "explore" : "ambient";
  document.getElementById("hint").textContent = on
    ? "" : "tap a memory to explore its pathways";
  clearInterval(ambientTimer);
  if (on) {
    document.getElementById("panel").classList.add("hidden");
    document.getElementById("catPanel").classList.add("hidden");
    ambientTimer = setInterval(() => {
      const neighbors = edges
        .filter((e) => e.a === focusId || e.b === focusId)
        .map((e) => (e.a === focusId ? e.b : e.a));
      const pool = neighbors.length ? neighbors : [...nodes.keys()];
      const next = pool.length
        ? pool[Math.floor(Math.random() * pool.length)] : null;
      if (next != null) {
        const n = nodes.get(next);
        if (n) n.glow = 1;
        setTimeout(() => {
          view === "categories" ? openCategory(next) : focusPhoto(next);
        }, 700);
      }
    }, 6000);
  }
}

document.getElementById("modeBtn").addEventListener("click", () =>
  setAmbient(!ambient)
);

/* ---------- memories: a slideshow that walks the memory graph ----------
   Not a random shuffle — each next photo is CONNECTED to the current one,
   and the caption says why ("same day", "same people"). Seeds are weighted
   toward on-this-day anniversaries (server-side). Built for a tablet or TV
   left running on a shelf: /memories, or the "memories" button. */
const MEM_DWELL_MS = 9000;
const MEM_RECENT_LIMIT = 300;   // 40 let a sparse edge pocket loop visibly
let memTimer = null;
let memCurrent = null;
let memRecent = [];
let memLayerA = true;

const MEM_REL_PRIORITY = ["same-event", "same-person", "same-place", "similar", "near-time"];

async function memNext() {
  // walk an edge from the current memory; fall back to a fresh seed
  let nextId = null, why = "";
  if (memCurrent != null) {
    try {
      const res = await fetch(`/api/neighborhood?id=${memCurrent}`);
      if (res.ok) {
        const d = await res.json();
        const cands = (d.edges || [])
          .filter((e) => e.a === memCurrent || e.b === memCurrent)
          .map((e) => ({ id: e.a === memCurrent ? e.b : e.a, rel: e.relation }))
          .filter((c) => !memRecent.includes(c.id));
        cands.sort((x, y) =>
          MEM_REL_PRIORITY.indexOf(x.rel) - MEM_REL_PRIORITY.indexOf(y.rel));
        if (cands.length) {
          const pick = cands[Math.floor(Math.random() * Math.min(3, cands.length))];
          nextId = pick.id;
          why = REL_LABELS[pick.rel] || pick.rel;
        }
      }
    } catch (e) { /* transient */ }
  }
  if (nextId == null) {
    try {
      const res = await fetch("/api/start");
      if (res.ok) {
        const d = await res.json();
        if (d.id != null && d.id !== memCurrent) {
          nextId = d.id;
          const fresh = ["a new memory", "from the archives",
                         "remember this?", "one more moment"];
          why = fresh[Math.floor(Math.random() * fresh.length)];
        }
      }
    } catch (e) { /* transient */ }
  }
  if (nextId == null) return;
  await memShow(nextId, why);
}

function memPreload(url) {
  return new Promise((resolve) => {
    const im = new Image();
    im.onload = () => resolve(true);
    im.onerror = () => resolve(false);
    im.src = url;
  });
}

let memSkips = 0;
async function memShow(id, why) {
  let d;
  try {
    const res = await fetch(`/api/photo?id=${id}`);
    if (!res.ok) return;
    d = await res.json();
  } catch (e) { return; }
  // never surface curated Trash in the memories stream
  if ((d.tags?.curation || []).includes("Trash")) {
    memRecent.push(id);
    if (memSkips++ < 5) { memCurrent = null; memNext(); }
    return;
  }
  memSkips = 0;
  const url = d.display || d.thumb;
  await memPreload(url);

  const showEl = document.getElementById(memLayerA ? "memA" : "memB");
  const hideEl = document.getElementById(memLayerA ? "memB" : "memA");
  memLayerA = !memLayerA;
  showEl.src = url;
  showEl.classList.add("showing");
  hideEl.classList.remove("showing");

  document.getElementById("memWhy").textContent = why ? `— ${why} —` : "";
  document.getElementById("memDate").textContent = d.taken_at
    ? new Date(d.taken_at).toLocaleDateString(undefined,
        { year: "numeric", month: "long", day: "numeric" })
    : "sometime worth remembering";
  const tags = document.getElementById("memTags");
  tags.innerHTML = "";
  let shown = 0;
  for (const values of Object.values(d.tags || {})) {
    for (const v of values) {
      if (shown >= 5) break;
      const b = document.createElement("b");
      b.textContent = v;
      tags.appendChild(b);
      shown++;
    }
  }

  memCurrent = id;
  memRecent.push(id);
  if (memRecent.length > MEM_RECENT_LIMIT) memRecent.shift();
}

function startMemories() {
  document.body.classList.add("memories");
  document.getElementById("memories").classList.remove("hidden");
  clearInterval(memTimer);
  memCurrent = null;
  memNext();
  memTimer = setInterval(memNext, MEM_DWELL_MS);
}

function stopMemories() {
  clearInterval(memTimer);
  memTimer = null;
  document.body.classList.remove("memories");
  document.getElementById("memories").classList.add("hidden");
}

document.getElementById("memBtn").addEventListener("click", () => {
  cancelKiosk();
  startMemories();
});
document.getElementById("memories").addEventListener("click", () => {
  if (location.pathname === "/memories") { memNext(); return; } // kiosk: tap = next
  cancelKiosk();
  stopMemories();
});
document.getElementById("memRemove").addEventListener("click", async (e) => {
  e.stopPropagation();               // never cancel the kiosk / close the show
  if (memCurrent == null) return;
  const id = memCurrent;
  try { await fetch(`/api/photo/remove?id=${id}`); } catch (err) {}
  memRecent.push(id);
  memCurrent = null;                 // don't walk edges from a removed photo
  document.getElementById("memWhy").textContent = "— removed (restorable in curation) —";
  memNext();
});

/* ---------- kiosk default: 1 min sphere, 5 min memories, forever;
   any real interaction cancels the autopilot ---------- */
var kiosk = location.pathname === "/";
var kioskTimer = null;

function kioskNet() {
  if (!kiosk) return;
  stopMemories();
  kioskTimer = setTimeout(kioskShow, 60 * 1000);        // 1 min of the sphere
}
// The full loop (persisted across the wall page): sphere 1m -> slideshow 5m ->
// sphere 1m -> wall 5m -> repeat. `kioskNext` alternates what comes after the
// sphere; the wall (a separate page) returns here and we pick up at slideshow.
function kioskShow() {
  if (!kiosk) return;
  const next = localStorage.getItem("kioskNext") || "slideshow";
  if (next === "wall") {
    localStorage.setItem("kioskNext", "slideshow");     // after the wall: slideshow
    location.href = "/wall?kiosk=1" + (LITE ? "&lite=1" : "");  // wall 5m -> /
    return;
  }
  localStorage.setItem("kioskNext", "wall");            // after slideshow: wall
  startMemories();                                       // slideshow for 5 min...
  kioskTimer = setTimeout(kioskNet, 5 * 60 * 1000);     // ...then back to the sphere
}
function cancelKiosk() {
  if (!kiosk) return;
  kiosk = false;
  clearTimeout(kioskTimer);
}

/* ---------- boot ---------- */
(async function boot() {
  loop(); // animate immediately; neurons appear as soon as data lands
  // A pipeline batch can briefly 503 the API — retry instead of dying on a
  // blank canvas (a first paint mid-ingest bricked the page on mobile).
  for (;;) {
    try {
      await loadCategories();
      break;
    } catch (e) {
      /* transient — retry */
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  if (ambient) setAmbient(true);
  if (location.pathname === "/memories") startMemories();
  if (kiosk) kioskTimer = setTimeout(kioskShow, 60 * 1000);
  // debug hook: ?taptest=1 simulates tapping the first neuron (headless QA)
  if (location.search.includes("taptest")) {
    const n = nodes.values().next().value;
    if (n) handleTap(n.x, n.y);
  }
})();
