// Constellation Setup — wizard flow (renderer).
// One plain question per screen (approved mock, 2026-10-02):
//   1 Where are your photos?  (Let's scan for your photos | I'll pick a folder)
//   2 Where should Constellation keep its copy?
//   3 Pick a family PIN.
//   4 Pick a backup drive.  (or Skip for now — consequence stated)
//   5 Your sky is filling up.  (this install's own links; AI descriptions opt-in)
// Nothing is written until "Start my sky". Nothing touches the graphics card
// unless the family says yes to describing their photos.
"use strict";
const S = window.setup;                 // preload bridge
const D = window.decide;                // pure decision helpers (decide.js)
const $ = (id) => document.getElementById(id);

const state = {
  screen: "where",
  sys: null,                // system scan: { sys, gpu, storage, network }
  sysError: null,
  route: null,              // "scan" | "pick"
  places: [], partial: false, ticks: {},
  picked: null,             // summarized picked folder
  keep: "default", libDefault: "", libOther: "", libError: null, libCheckedFor: null,
  backupChoice: null,       // drive path | "other" | "skip"
  backupOther: "",
  finish: null, lan: null, swept: 0, sweepTotal: 0,
  error: null, describeState: "off",   // off | working | on | failed
  log: [],
  cfg: { model: "qwen2.5vl:7b", mode: "cpu", libraryRoot: "", sources: [],
         vaultMode: "dir", backupTarget: "", updates: true, describe: false },
};
// The PIN lives only here, only until "Start my sky" hands it to the backend
// (which hashes it). Never in state, the config file, logs or details.
let pin = "", pin2 = "";

const ORDER = ["where", "keep", "pin", "backup", "sky"];
const STEP_OF = { where: 0, scanning: 0, found: 0, pick: 0, keep: 1, pin: 2, backup: 3, sky: 4 };

function esc(s) { return String(s == null ? "" : s).replace(/[<>&"']/g, (c) =>
  ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c])); }
const fmt = (n) => Number(n || 0).toLocaleString("en-US");
const years = (p) => p.firstYear == null ? "" : p.firstYear === p.lastYear ? String(p.firstYear) : `${p.firstYear}–${p.lastYear}`;
const drives = () => (state.sys && state.sys.storage && state.sys.storage.drives) || [];
const joinPath = (dir, name) => dir.replace(/[\\/]+$/, "") + (dir.includes("\\") ? "\\" : "/") + name;
function log(line) { state.log.push(line); if (state.log.length > 200) state.log.shift(); syncDetails(); }

S.onProgress((pr) => {
  if (pr && pr.msg) log(`[${pr.phase}] ${pr.msg}`);
  // the patient sky: count photos as the first sweep reads them
  if (pr && pr.phase === "sweep" && pr.count != null) {
    state.swept = pr.count;
    const el = $("cnt"); if (el) el.textContent = fmt(pr.count);
    const line = $("cntline");
    if (line) line.textContent = `photos so far · from ${sourceNames().join(", ")}`;
  }
});

// --- sources and checks ------------------------------------------------------
function tickedPlaces() { return state.places.filter((p) => state.ticks[p.path]); }
function sources() { return state.route === "pick" ? (state.picked ? [state.picked.path] : []) : tickedPlaces().map((p) => p.path); }
function sourceCount() {
  return state.route === "pick" ? (state.picked ? state.picked.photos : 0)
    : tickedPlaces().reduce((a, p) => a + p.photos, 0);
}
function sourceNames() {
  return state.route === "pick" ? [state.picked ? state.picked.name : ""]
    : tickedPlaces().map((p) => p.name);
}
function libraryRoot() { return state.keep === "other" ? state.libOther : state.libDefault; }
// Pictures in the home folder is where a computer keeps photos: the one place
// pre-ticked, and the screen says why.
function preTicked(p) { return p.wellKnown && /^pictures$/i.test(p.name) && p.rootLabel === "Home"; }
function pinOk() { return !D.validatePin(pin, pin2); }

// --- shell -------------------------------------------------------------------
let cur = null;
function go(screen) { state.screen = screen; render(); $("main").focus(); }
function syncDetails() {
  const el = $("detailText");
  if (!el || !cur) return;
  const facts = typeof cur.details === "function" ? cur.details() : (cur.details || "");
  el.textContent = facts + (state.log.length ? "\n\n" + state.log.slice(-12).join("\n") : "");
}
function syncNav() {
  const n = $("next"), b = $("back"), x = $("extra");
  if (!cur.next) n.hidden = true;
  else {
    n.hidden = false; n.textContent = cur.next.label;
    n.disabled = cur.next.ok ? !cur.next.ok() : false;
    n.onclick = cur.next.fn;
  }
  b.hidden = !cur.back; b.onclick = cur.back || null;
  x.hidden = !cur.extra;
  if (cur.extra) { x.textContent = cur.extra.label; x.onclick = cur.extra.fn; }
  syncDetails();
}
function render() {
  cur = SCREENS[state.screen]();
  $("main").innerHTML = cur.html;
  const step = STEP_OF[state.screen];
  $("steps").innerHTML = `<span>${step + 1} of 5</span>` +
    ORDER.map((_, i) => `<i class="${i < step ? "done" : i === step ? "on" : ""}"></i>`).join("");
  syncNav();
  if (cur.wire) cur.wire();
}

// --- screens -----------------------------------------------------------------
const SCREENS = {
  where() {
    const s = state.sys && state.sys.sys;
    const lowRam = s && Number(s.ramGB) < D.MIN_RAM_GB;
    return { html: `
      <h2 class="q">Where are your photos?</h2>
      <p class="lede">Constellation brings your photos into one sky. Start by showing it where they are.</p>
      ${lowRam ? `<p class="msg stop">This computer has ${esc(s.ramGB)} GB of memory. Constellation needs at least ${D.MIN_RAM_GB} GB, so it can't run here. Try a computer with more memory.</p>` : ""}
      <div class="choices">
        <button class="choice" id="cScan" type="button" ${lowRam ? "disabled" : ""}>
          <svg viewBox="0 0 34 34" aria-hidden="true"><circle cx="15" cy="15" r="9" fill="none" stroke="#8fd4ff" stroke-width="2"/><path d="M22 22 L30 30" stroke="#8fd4ff" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="13" r="1.4" fill="#f2c36b"/><circle cx="17" cy="16" r="1.1" fill="#e4eef8"/></svg>
          <span class="t">Let's scan for your photos</span>
          <span class="d">Constellation looks through this computer and any plugged-in drives, then shows you what it found. It only looks. Nothing is copied until you choose.</span>
        </button>
        <button class="choice" id="cPick" type="button" ${lowRam ? "disabled" : ""}>
          <svg viewBox="0 0 34 34" aria-hidden="true"><path d="M4 9 h9 l3 3 h14 v15 h-26 z" fill="none" stroke="#8fd4ff" stroke-width="2" stroke-linejoin="round"/></svg>
          <span class="t">I'll pick a folder</span>
          <span class="d">You already know where they are. Choose the folder and Constellation shows you a few photos so you can check it's the right one.</span>
        </button>
      </div>
      <p class="quiet">Not sure? Scanning is the easy way. You can add more folders later.</p>`,
      details: () => state.sys ? `This computer: ${state.sys.sys.ramGB} GB memory, ${state.sys.sys.cores} cores, ${drives().length} drives.\nScanning is read-only.`
        : state.sysError ? `System check failed: ${state.sysError}` : "Checking this computer…",
      wire() {
        $("cScan").onclick = () => { state.route = "scan"; go("scanning"); };
        $("cPick").onclick = () => pickFolderFlow();
      } };
  },

  scanning() {
    return { html: `
      <h2 class="q">Looking for your photos…</h2>
      <p class="lede">This only looks. Nothing is copied, moved or changed.</p>
      <div class="scan">
        <div class="meter" aria-hidden="true"><span></span></div>
        <p class="scanline" id="scanline">Starting…</p>
        <p class="quiet">Skipping system folders, app and program files, and Constellation's own folder.</p>
      </div>`,
      details: "Read-only: the home folder and plugged-in drives.\nStops on its own after about half a minute.",
      back: () => go("where"),
      async wire() {
        const r = await S.scanPhotos({});
        if (state.screen !== "scanning") return;          // the family went back
        if (!r.ok) { state.places = []; state.error = r.error; log("scan failed: " + r.error); }
        else {
          state.places = r.places; state.partial = r.partial; state.error = null;
          state.ticks = {};
          for (const p of r.places) state.ticks[p.path] = preTicked(p);
          log(`scan: ${r.places.length} places${r.partial ? " (stopped early)" : ""}`);
        }
        go("found");
      } };
  },

  found() {
    const n = state.places.length;
    if (!n) {
      return { html: `
        <h2 class="q">We didn't find photos on this computer.</h2>
        <p class="lede">${state.error ? "The scan couldn't finish. " : ""}If you know where they are, pick the folder yourself.</p>`,
        details: state.error ? `Scan error: ${state.error}` : "No folder held 20 or more photos.",
        next: { label: "Pick a folder", fn: () => pickFolderFlow() }, back: () => go("where") };
    }
    const rows = state.places.map((p, i) => `
      <label class="folder" for="f${i}">
        <input type="checkbox" id="f${i}" data-path="${esc(p.path)}" ${state.ticks[p.path] ? "checked" : ""}>
        <span class="name">${esc(p.rootLabel === "Home" ? p.name : `${p.rootLabel} › ${p.name}`)}
          <small>${fmt(p.photos)} photos${p.videos ? ` · ${fmt(p.videos)} videos` : ""}${years(p) ? ` · ${years(p)}` : ""} · ${p.rootLabel === "Home" ? "Your computer" : "Drive"}</small>
          ${preTicked(p) ? `<span class="why">Ticked because this is where your computer keeps photos. Untick it if you'd rather not.</span>` : ""}</span>
        <span class="thumbs">${(p.thumbs || []).map((t) => `<img alt="" src="${esc(t)}">`).join("")}</span>
      </label>`).join("");
    return { html: `
      <h2 class="q">We found photos in ${n === 1 ? "one place" : `${n} places`}.</h2>
      <p class="lede">Tick the ones that belong in your sky. Nothing is brought in until you continue.</p>
      <div class="found" id="found">${rows}</div>
      <p class="quiet"><span class="total" id="total"></span>${state.partial ? " · We stopped looking after a while; if a folder is missing, add it." : ""}</p>
      <p class="quiet"><button class="linkbtn" id="addMore" type="button">Add another folder</button></p>`,
      details: () => "Selected:\n" + (tickedPlaces().map((p) => "  " + p.path).join("\n") || "  (none)"),
      next: { label: "Use these photos", fn: () => go("keep"), ok: () => tickedPlaces().length > 0 },
      back: () => go("where"),
      wire() {
        const upd = () => {
          const c = sourceCount();
          $("total").textContent = tickedPlaces().length ? `${fmt(c)} photos selected` : "Nothing selected yet";
          syncNav();
        };
        document.querySelectorAll("#found input[type=checkbox]").forEach((cb) => {
          cb.onchange = () => { state.ticks[cb.dataset.path] = cb.checked; upd(); };
        });
        $("addMore").onclick = async () => {
          const p = await S.pickFolder("Choose a photo folder");
          if (!p) return;
          const r = await S.summarizeFolder(p);
          if (!r.ok) return;
          const pl = { ...r.place, rootLabel: "Home", name: r.place.name, thumbs: (r.place.thumbs || []).slice(0, 4) };
          if (!state.places.some((x) => x.path === pl.path)) state.places.push(pl);
          state.ticks[pl.path] = true;
          render();
        };
        upd();
      } };
  },

  pick() {
    const p = state.picked;
    return { html: `
      <h2 class="q">Is this the right folder?</h2>
      <div class="path"><span class="mono">${esc(p.path)}</span></div>
      ${p.photos ? `<p class="lede"><span class="total">${fmt(p.photos)} photos</span>${years(p) ? ` from ${years(p)}` : ""}. Here are a few of them.</p>
        <div class="big-thumbs">${(p.thumbs || []).map((t) => `<img alt="" src="${esc(t)}">`).join("")}</div>`
        : `<p class="msg warn">We didn't find any photos in this folder. Choose another one.</p>`}
      <p class="quiet">Wrong folder? Choose another. Nothing has been copied.</p>`,
      details: `Chosen folder: ${p.path}\nRead-only preview.`,
      next: { label: "Yes, use this folder", fn: () => go("keep"), ok: () => p.photos > 0 },
      back: () => go("where"),
      extra: { label: "Choose another", fn: () => pickFolderFlow() } };
  },

  keep() {
    const lib = libraryRoot();
    const drive = D.pickDriveFor(lib, drives());
    const floor = state.sys ? D.hardwareFloor(state.sys.sys, drives(), lib) : { ok: true };
    const freeLine = drive && drive.freeGB != null ? `${esc(D.storageMath(drive.freeGB))}` : "";
    return { html: `
      <h2 class="q">Where should Constellation keep its copy?</h2>
      <p class="promise"><svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M3 9.5 L7 13 L15 5" fill="none" stroke="#6fd3a0" stroke-width="2"/></svg>Your originals are never moved or changed.</p>
      <div class="opts">
        <label class="opt" for="k1"><input type="radio" name="keep" id="k1" ${state.keep === "default" ? "checked" : ""}>
          <span>A new folder: <span class="mono">${esc(state.libDefault)}</span><small>Recommended.${state.keep === "default" && freeLine ? " " + freeLine : ""}</small></span></label>
        <label class="opt" for="k2"><input type="radio" name="keep" id="k2" ${state.keep === "other" ? "checked" : ""}>
          <span>A new folder somewhere else${state.libOther ? `: <span class="mono">${esc(state.libOther)}</span>` : ""}<small>${state.keep === "other" && freeLine ? freeLine : "Choose a drive or folder. Constellation makes its own folder there."}</small></span></label>
      </div>
      ${state.libError ? `<p class="msg stop" role="alert">${esc(state.libError)}</p>` : ""}
      ${!state.libError && !floor.ok ? `<p class="msg stop" role="alert">${esc(floor.problems.join(" "))} Choose “A new folder somewhere else” on a bigger drive.</p>` : ""}`,
      details: () => `Library: ${lib || "(not chosen)"} (will be created)\nPhotos from: ${sources().join(", ")}`,
      next: { label: "Continue", fn: () => go("pin"), ok: () => !!lib && !state.libError && floor.ok },
      back: () => go(state.route === "pick" ? "pick" : "found"),
      wire() {
        if (state.libCheckedFor !== libraryRoot()) checkLibrary();
        $("k1").onchange = () => { state.keep = "default"; checkLibrary(); };
        $("k2").onchange = async () => {
          const p = await S.pickFolder("Choose where Constellation should make its folder");
          if (p) { state.libOther = joinPath(p, "Constellation"); state.keep = "other"; }
          checkLibrary();
        };
      } };
  },

  pin() {
    return { html: `
      <h2 class="q">Pick a family PIN.</h2>
      <p class="lede">Grown-ups use it to open the private pages: cleanup, people and settings. The wall and the TV never ask for it.</p>
      <div class="field"><label for="pin1">PIN, 4 to 8 digits</label>
        <div class="pin"><input id="pin1" type="password" inputmode="numeric" autocomplete="new-password" maxlength="8" aria-label="PIN">
        <input id="pin2" type="password" inputmode="numeric" autocomplete="new-password" maxlength="8" aria-label="Type it again" placeholder="again"></div></div>
      <p class="quiet" id="pinmsg"></p>`,
      details: "Stored as a salted hash inside the library. Never sent anywhere, never written to the settings file.",
      next: { label: "Continue", fn: () => go("backup"), ok: pinOk },
      back: () => go("keep"),
      wire() {
        const msg = () => {
          const e = pin && pin2 && pin2.length >= pin.length ? D.validatePin(pin, pin2) : null;
          $("pinmsg").innerHTML = e ? `<span style="color:var(--stop)">${esc(e)}</span>`
            : pinOk() ? `<span style="color:var(--ok)">PIN set.</span>` : "Type it twice so a typo can't lock you out.";
          syncNav();
        };
        const on = (id, set) => { const el = $(id); el.value = id === "pin1" ? pin : pin2;
          el.oninput = () => { el.value = el.value.replace(/\D/g, ""); set(el.value); msg(); }; };
        on("pin1", (v) => { pin = v; }); on("pin2", (v) => { pin2 = v; });
        msg();
      } };
  },

  backup() {
    const rem = drives().filter((d) => d.removable);
    const target = state.backupChoice === "skip" ? "" : state.backupChoice === "other" ? state.backupOther : (state.backupChoice || "");
    const bkErr = target ? D.validateBackupTarget(target, libraryRoot(), drives(), sources()) : null;
    return { html: `
      <h2 class="q">Pick a backup drive.</h2>
      <p class="lede">Constellation copies your sky to it every night, so one broken drive can't take your photos with it.</p>
      <div class="opts">
        ${rem.map((d, i) => `<label class="opt" for="b${i}"><input type="radio" name="bk" id="b${i}" data-bk="${esc(d.path)}" ${state.backupChoice === d.path ? "checked" : ""}>
          <span>${esc(d.label || d.path)} (USB drive)<small>${d.freeGB != null ? `${fmt(d.freeGB)} GB free. ` : ""}Leave it plugged in.</small></span></label>`).join("")}
        <label class="opt" for="bOther"><input type="radio" name="bk" id="bOther" ${state.backupChoice === "other" ? "checked" : ""}>
          <span>${state.backupOther ? `Another drive: <span class="mono">${esc(state.backupOther)}</span>` : "Another drive or folder"}<small>${rem.length ? "" : "No USB drive is plugged in. You can plug one in now, or "}Choose where backups go.</small></span></label>
        <label class="opt" for="bSkip"><input type="radio" name="bk" id="bSkip" ${state.backupChoice === "skip" ? "checked" : ""}>
          <span>Skip for now<small>You can add a drive later in Settings.</small></span></label>
      </div>
      ${state.backupChoice === "skip" ? `<p class="msg warn">Until you add a drive, your sky has one copy, on this computer. If this computer's drive fails, that copy is gone. Your originals stay where they are.</p>` : ""}
      ${bkErr ? `<p class="msg stop" role="alert">${esc(bkErr)}</p>` : ""}
      ${target && !bkErr ? `<p class="msg ok">Backups go to ${esc(target)}. Constellation doesn't read photos from it.</p>` : ""}
      ${state.error ? `<p class="msg stop" role="alert">${esc(state.error)}</p>` : ""}`,
      details: () => target ? `Backup target: ${target}` : state.backupChoice === "skip" ? "No backup drive yet" : "Nothing chosen",
      next: { label: "Start my sky", fn: () => startSky(), ok: () => !!state.backupChoice && !bkErr && (state.backupChoice !== "other" || !!state.backupOther) },
      back: () => go("pin"),
      wire() {
        document.querySelectorAll("[data-bk]").forEach((r) => { r.onchange = () => { state.backupChoice = r.dataset.bk; state.error = null; render(); }; });
        $("bOther").onchange = async () => {
          const p = await S.pickFolder("Choose the backup drive or folder");
          if (p) { state.backupOther = p; state.backupChoice = "other"; }
          state.error = null; render();
        };
        $("bSkip").onchange = () => { state.backupChoice = "skip"; state.error = null; render(); };
      } };
  },

  sky() {
    const total = sourceCount();
    const r = state.finish;
    const u = r && r.ok ? D.finishUrls(state.lan && state.lan.address, r.httpPort, r.tlsPort) : null;
    const copy = r && r.ok ? D.finishCopy(r) : null;
    const descLabel = state.describeState === "working" ? "Downloading the photo reader (about 6 GB)…"
      : state.describeState === "on" ? "Describing your photos overnight. You can turn this off later in Settings."
      : state.describeState === "failed" ? "The photo reader couldn't be set up. Your sky works without it."
      : `Want Constellation to describe your photos? It downloads a 6 GB photo reader once and uses this computer's ${state.cfg.mode === "gpu" ? "graphics card" : "processor"} overnight.`;
    return { html: `
      <h2 class="q">Your sky is filling up.</h2>
      <div class="filling">
        <div style="display:grid;gap:10px;min-width:0">
          <canvas class="mini" id="mini" width="640" height="400" aria-label="Your photos appearing as stars"></canvas>
          <div><div class="count" id="cnt">${fmt(state.swept)}</div>
            <p class="quiet" id="cntline">${state.swept ? "photos so far" : "Starting…"} · from ${esc(sourceNames().join(", "))}${total ? ` (about ${fmt(total)})` : ""}</p></div>
        </div>
        <div class="links">
          ${state.error ? `<p class="msg stop" role="alert">${esc(state.error)}</p>` : ""}
          ${u ? `
          <div class="link"><span class="k">On the TV</span>
            <div class="row"><span class="mono">${esc(u.tv)}</span><button class="copy" type="button" data-copy="${esc(u.tv)}">Copy</button></div>
            <span class="quiet">Type this into the TV's browser. No PIN, it's a picture frame.</span></div>
          <div class="link"><span class="k">On phones</span>
            <div class="row"><span class="mono">${esc(u.app)}</span><button class="copy" type="button" data-copy="${esc(u.app)}">Copy</button></div>
            <span class="quiet">Grown-ups sign in with the family PIN. The first time on each phone, install the family certificate from <span class="mono">${esc(u.ca)}</span>.</span></div>
          ${state.lan && state.lan.address ? "" : `<p class="quiet">This computer isn't on a home network right now, so these only work on this computer.</p>`}
          <div class="optin">
            <span style="flex:1;min-width:200px">${esc(descLabel)}</span>
            ${state.describeState === "off" ? `<span class="seg" role="group" aria-label="Describe photos"><button type="button" id="dNo" aria-pressed="true">Not now</button><button type="button" id="dYes" aria-pressed="false">Yes, overnight</button></span>` : ""}
          </div>
          <p class="quiet">${esc(copy.startup)} ${esc(copy.progress)} Only the photos you chose.</p>`
          : !state.error ? `<p class="quiet">Reading your newest photos first so you can see them tonight. The rest arrive while you sleep.</p>` : ""}
        </div>
      </div>`,
      details: () => r && r.ok ? `Server: this install only (verified by its own family certificate)\nPorts: ${r.httpPort} (wall) · ${r.tlsPort} (private pages)\nAddress: ${(state.lan && state.lan.address) || "this computer only"}\nLibrary: ${state.cfg.libraryRoot}\nDescriptions: ${state.describeState}`
        : `Library: ${state.cfg.libraryRoot}`,
      next: r && r.ok ? { label: "Open the sky", fn: () => S.openWall() }
        : state.error ? { label: "Try again", fn: () => finishFlow() } : null,
      wire() {
        drawMini();
        document.querySelectorAll(".copy").forEach((b) => { b.onclick = async () => {
          try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copied"; }
          catch { const rg = document.createRange(); rg.selectNodeContents(b.previousElementSibling);
            const sel = getSelection(); sel.removeAllRanges(); sel.addRange(rg); b.textContent = "Selected"; }
        }; });
        const yes = $("dYes");
        if (yes) yes.onclick = () => enableDescribe();
      } };
  },
};

// --- flows -------------------------------------------------------------------
async function pickFolderFlow() {
  const p = await S.pickFolder("Choose your photo folder");
  if (!p) return;
  state.route = "pick";
  const r = await S.summarizeFolder(p);
  state.picked = r.ok ? r.place : { path: p, name: p, photos: 0, thumbs: [] };
  log(`picked: ${p} (${state.picked.photos} photos)`);
  go("pick");
}

async function checkLibrary() {
  state.libCheckedFor = libraryRoot();
  const r = await S.libraryCheck({ libraryRoot: libraryRoot(), sources: sources(), backupTarget: state.cfg.backupTarget });
  state.libError = r && r.error ? r.error : null;
  render();
}

async function startSky() {
  const c = state.cfg;
  c.libraryRoot = libraryRoot();
  c.sources = sources();
  c.backupTarget = state.backupChoice === "skip" ? "" : state.backupChoice === "other" ? state.backupOther : state.backupChoice;
  $("next").disabled = true;
  // The PIN goes straight to the backend, which hashes it.
  const r = await S.prepare({ ...c, pin });
  pin = ""; pin2 = "";
  if (!r.ok) {
    if (r.detail) log("prepare failed: " + r.detail);
    if (r.field === "lib") { state.libError = r.error; go("keep"); return; }
    state.error = r.error || "Setup couldn't start."; render(); return;
  }
  log("prepare: library, family PIN and family certificate ready");
  state.error = null;
  go("sky");
  const sw = await S.sweep(c);
  if (!sw.ok) { state.error = "Constellation couldn't read your photos: " + (sw.error || "unknown error"); render(); return; }
  await finishFlow();
}

async function finishFlow() {
  state.error = null; render();
  const r = await S.finish(state.cfg);
  state.finish = r;
  if (!r.ok) {
    // Never a fake success: say what happened and what to do.
    state.error = `${r.headline || "Constellation didn't start."} ${r.error || ""} ${r.retryHint || ""}`.trim();
  } else {
    state.lan = await S.lanAddress();
  }
  render();
}

async function enableDescribe() {
  state.describeState = "working"; render();
  const r = await S.enableDescribe({ ...state.cfg, describe: true });
  if (r && r.ok) { state.cfg.describe = true; state.describeState = "on"; }
  else { state.describeState = "failed"; log("describe: " + ((r && r.error) || "failed")); }
  render();
}

// --- the little sky that fills with your photos -------------------------------
function drawMini() {
  const c = $("mini"); if (!c) return;
  const g = c.getContext("2d");
  let seed = 7; const rnd = () => ((seed = (seed * 9301 + 49297) % 233280) / 233280);
  const stars = Array.from({ length: 260 }, () => ({ x: rnd() * 640, y: rnd() * 400, s: 0.6 + rnd() * 1.8, h: rnd() }));
  const edges = Array.from({ length: 40 }, () => [Math.floor(rnd() * 260), Math.floor(rnd() * 260)]);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const frame = () => {
    if ($("mini") !== c) return;
    const total = Math.max(1, sourceCount());
    const target = state.finish && state.finish.ok ? 1 : Math.min(1, state.swept / total);
    const lit = Math.floor(stars.length * (reduce ? target : Math.max(0.04, target)));
    g.fillStyle = "#030812"; g.fillRect(0, 0, 640, 400);
    g.strokeStyle = "#8fd4ff22"; g.lineWidth = 1;
    edges.forEach(([a, b]) => { if (a < lit && b < lit) { g.beginPath(); g.moveTo(stars[a].x, stars[a].y); g.lineTo(stars[b].x, stars[b].y); g.stroke(); } });
    stars.forEach((s, i) => { if (i >= lit) return; g.fillStyle = s.h > 0.85 ? "#f2c36b" : s.h > 0.5 ? "#8fd4ff" : "#e4eef8";
      g.beginPath(); g.arc(s.x, s.y, s.s, 0, 7); g.fill(); });
    if (!reduce) requestAnimationFrame(frame);
  };
  frame();
}

// --- start ---------------------------------------------------------------------
S.onScanProgress((p) => {
  const el = $("scanline");
  if (el && p && p.dirs != null) el.textContent = `Looked in ${fmt(p.dirs)} folders so far…`;
});
(async () => {
  render();
  try {
    state.libDefault = (await S.defaults()).libraryRoot;
    const r = await S.scan();
    if (r.ok) {
      state.sys = r.data;
      state.cfg.mode = D.recommendMode(r.data.gpu, r.data.sys).mode;
      log(`system: ${r.data.sys.ramGB} GB RAM, ${r.data.sys.cores} cores, gpu=${(r.data.gpu && r.data.gpu.name) || "none"}`);
    } else state.sysError = r.error;
  } catch (e) { state.sysError = String(e); }
  if (state.screen === "where") render(); else syncDetails();
})();
