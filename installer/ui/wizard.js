// Constellation Setup — wizard flow (renderer). V2 CP2.
// Six steps (spec A1): Welcome → System scan → Storage & sources → Downloads
// → First sweep → Finish. Plain words, one decision per screen, sensible
// defaults. "Show details" reveals paths, model choice and a live log tail.
"use strict";
const S = window.setup;                 // preload bridge
const D = window.decide;                // pure decision helpers (decide.js)
const main = document.getElementById("main");
const stepsEl = document.getElementById("steps");
const backBtn = document.getElementById("back");
const nextBtn = document.getElementById("next");
const techPanel = document.getElementById("techPanel");
const techLog = document.getElementById("techlog");

const state = {
  scan: null,
  cfg: { model: "qwen2.5vl:7b", mode: "cpu", libraryRoot: "", sources: [],
         vaultMode: "dir", backupTarget: "", pinSet: false, updates: true },
};
const STEPS = ["welcome", "scan", "storage", "download", "sweep", "done"];
let step = 0;

function gb(n) { return (n == null) ? "?" : `${n} GB`; }
function esc(s) { return String(s == null ? "" : s).replace(/[<>&"']/g, (c) =>
  ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c])); }
function log(line) {
  techLog.textContent += line + "\n";
  const lines = techLog.textContent.split("\n");
  if (lines.length > 400) techLog.textContent = lines.slice(-400).join("\n");
  techLog.scrollTop = techLog.scrollHeight;
}
function showTech() {
  const c = state.cfg;
  techPanel.innerHTML = `<div class="card" style="margin-top:0.5rem">
    <div class="row"><span class="k">Library</span><span class="v"><code>${esc(c.libraryRoot || "—")}</code></span></div>
    <div class="row"><span class="k">Backup drive</span><span class="v"><code>${esc(c.backupTarget || "—")}</code></span></div>
    <div class="row"><span class="k">AI model</span><span class="v"><code>${esc(c.model)}</code> on ${esc(c.mode.toUpperCase())}</span></div>
    <div class="row"><span class="k">Sources</span><span class="v">${c.sources.length}</span></div>
    <div class="row"><span class="k">Updates</span><span class="v">${c.updates ? "3–5 AM, automatic" : "manual only"}</span></div>
  </div>`;
}
S.onProgress((pr) => { if (pr && pr.msg) log(`[${pr.phase}] ${pr.msg}`); });

function renderSteps() {
  stepsEl.innerHTML = STEPS.map((_, i) =>
    `<div class="s ${i < step ? "done" : i === step ? "on" : ""}"></div>`).join("");
}
async function go(n) { step = Math.max(0, Math.min(STEPS.length - 1, n)); renderSteps(); showTech(); await render(); }
backBtn.onclick = () => go(step - 1);

async function render() {
  backBtn.style.visibility = step === 0 ? "hidden" : "visible";
  nextBtn.style.display = "";
  nextBtn.disabled = false;
  nextBtn.textContent = "Next →";
  main.innerHTML = "";
  await VIEWS[STEPS[step]]();
}

const VIEWS = {
  // 1) Welcome ---------------------------------------------------------------
  async welcome() {
    main.innerHTML = `
      <div class="big">
        <div class="icon">🌌</div>
        <h2 style="margin-top:0.6rem">Welcome to Constellation</h2>
        <p class="sub" style="max-width:540px;margin:0.6rem auto 0">
          Your family's photos, safe at home and alive on your walls.
          Everything stays on <b>this computer</b> and your home network. Nothing is sent to anyone.</p>
        <p class="sub" style="max-width:540px;margin:0.4rem auto 0">
          This takes about one evening. You'll pick where the photos live, set a family PIN,
          choose a backup drive, and then watch your sky fill up.</p>
      </div>`;
    nextBtn.textContent = "Get started →";
    nextBtn.onclick = () => go(1);
  },

  // 2) System scan -----------------------------------------------------------
  async scan() {
    main.innerHTML = `<h2>Checking this computer…</h2>
      <p class="sub">Memory, storage and your home network.</p><div class="spin"></div>`;
    nextBtn.disabled = true;
    const r = await S.scan();
    if (!r.ok) { main.innerHTML = `<h2>The check didn't finish</h2><p class="sub">${esc(r.error)}</p>`; return; }
    state.scan = r.data;
    const { sys, gpu, storage, network } = r.data;
    if (!state.cfg.libraryRoot) state.cfg.libraryRoot = (await S.defaults()).libraryRoot;
    const rec = D.recommendMode(gpu, sys);
    state.cfg.mode = rec.mode;
    const floor = D.hardwareFloor(sys, storage.drives, state.cfg.libraryRoot);
    log(`scan: ${sys.cores} cores, ${sys.ramGB} GB RAM, gpu=${gpu.name || "none"} (${gpu.accel}), drives=${storage.drives.length}`);

    main.innerHTML = `
      <h2>Here's what I found</h2>
      <p class="sub">Constellation doesn't need a graphics card. It runs on any spare PC with ${D.MIN_RAM_GB} GB of memory and about ${D.MIN_FREE_GB} GB free.</p>
      <div class="card">
        <div class="row"><span class="k">Memory</span><span class="v">${gb(sys.ramGB)}</span></div>
        <div class="row"><span class="k">Processor</span><span class="v">${esc(sys.cpu)} · ${sys.cores} cores</span></div>
        <div class="row"><span class="k">Free space for photos</span><span class="v">${gb(floor.freeGB)}</span></div>
        <div class="row"><span class="k">Other computers at home</span><span class="v">${network.machines.length}</span></div>
      </div>
      ${floor.ok ? `<div class="card rec"><span class="badge ok">ready</span>
          <p class="muted" style="margin-top:0.5rem">${esc(rec.note)}</p></div>`
        : `<div class="card problem">${floor.problems.map((p) => `<p>${esc(p)}</p>`).join("")}
          <p class="muted" style="margin-top:0.5rem">You can pick a bigger drive on the next screen.
          Buying hardware? <a href="#" id="shop">See the recommended list</a>.</p></div>`}
      ${rec.modes.length > 1 ? `<div class="card"><div class="row" style="align-items:center">
          <span class="k">Read photos using</span>
          <select id="modeSel" style="background:#06101c;border:1px solid #1c3e60;color:#dfeefa;border-radius:8px;padding:0.4rem 0.6rem;font:inherit">
            <option value="gpu" ${rec.mode === "gpu" ? "selected" : ""}>the graphics card (faster first night)</option>
            <option value="cpu" ${rec.mode === "cpu" ? "selected" : ""}>the processor (always works)</option>
          </select></div></div>` : ""}`;
    const shop = document.getElementById("shop");
    if (shop) shop.onclick = (e) => { e.preventDefault(); S.openUrl(floor.shoppingList); };
    const sel = document.getElementById("modeSel");
    if (sel) sel.onchange = () => { state.cfg.mode = sel.value; showTech(); };
    // Memory below the floor can't be fixed on the next screen; disk space can.
    const ramOk = Number(sys.ramGB) >= D.MIN_RAM_GB;
    nextBtn.disabled = !ramOk;
    if (!ramOk) nextBtn.textContent = "Needs more memory";
    nextBtn.onclick = () => go(2);
    showTech();
  },

  // 3) Storage, sources, PIN, backup -----------------------------------------
  async storage() {
    const { storage } = state.scan;
    const cands = storage.photoCandidates;
    const removable = storage.drives.filter((d) => d.removable);
    const free = () => {
      const d = D.pickDriveFor(state.cfg.libraryRoot, storage.drives);
      return d ? d.freeGB : null;
    };
    main.innerHTML = `
      <h2>Where should your photos live?</h2>
      <p class="sub">Photos are copied into the library. The folders you pick are only ever read, never changed.</p>
      <div class="card"><div class="pathrow">
        <input id="lib" value="${esc(state.cfg.libraryRoot)}" aria-label="Library location">
        <button class="ghost" id="pickLib">Change…</button></div>
        <p class="muted" id="math" style="margin-top:0.4rem"></p>
        <p class="err" id="libErr"></p></div>

      <h3 style="font-size:0.82rem;color:#7f9bb3;text-transform:uppercase;letter-spacing:0.1em;margin:0.9rem 0 0.4rem">Photos to bring in</h3>
      <div class="card list" id="srcList">
        ${cands.length ? cands.map((c, i) =>
          `<label><input type="checkbox" data-src="${esc(c)}" ${D.defaultSourceChecked(c, storage.drives) ? "checked" : ""}> ${esc(c)}</label>`).join("")
          : `<p class="muted">No photo folders found yet. Add one below.</p>`}
      </div>
      <button class="ghost" id="addSrc">+ Add a folder…</button>

      <h3 style="font-size:0.82rem;color:#7f9bb3;text-transform:uppercase;letter-spacing:0.1em;margin:0.9rem 0 0.4rem">Family PIN</h3>
      <div class="card pinrow">
        <p class="muted">Grown-ups use this to open the private pages: cleanup, people, the vault and settings.
          The wall and slideshows never ask for it.</p>
        <input id="pin1" type="password" inputmode="numeric" placeholder="PIN" autocomplete="new-password" aria-label="Family PIN">
        <input id="pin2" type="password" inputmode="numeric" placeholder="Same PIN again" autocomplete="new-password" aria-label="Family PIN again">
        <p class="err" id="pinErr"></p></div>

      <h3 style="font-size:0.82rem;color:#7f9bb3;text-transform:uppercase;letter-spacing:0.1em;margin:0.9rem 0 0.4rem">Backup drive</h3>
      <div class="card">
        <p class="muted">When phones free up space, this computer holds the only copy. A backup drive keeps it safe.
          Plug in an external drive (a 4 TB one is plenty).</p>
        <div class="list" id="bkList">${removable.map((d) =>
          `<label><input type="radio" name="bk" data-bk="${esc(d.path)}"> ${esc(d.label || d.path)}
             <span class="meta">${gb(d.freeGB)} free</span></label>`).join("") ||
          `<p class="muted">No external drive found. Plug one in, or choose a folder on another drive.</p>`}</div>
        <button class="ghost" id="pickBk">Choose a different drive…</button>
        <p class="err" id="bkErr"></p></div>

      <label style="display:flex;gap:0.5rem;align-items:center;margin-top:0.6rem;font-size:0.88rem">
        <input type="checkbox" id="upd" ${state.cfg.updates ? "checked" : ""}>
        Update Constellation automatically between 3 and 5 AM. It only downloads updates and sends nothing.</label>`;

    const showMath = () => {
      const f = free();
      document.getElementById("math").textContent = f == null ? "" : D.storageMath(f);
      const fl = D.hardwareFloor(state.scan.sys, storage.drives, state.cfg.libraryRoot);
      document.getElementById("libErr").textContent = fl.ok ? "" : fl.problems.join(" ");
    };
    showMath();
    document.getElementById("lib").onchange = (e) => { state.cfg.libraryRoot = e.target.value.trim(); showMath(); showTech(); };
    document.getElementById("pickLib").onclick = async () => {
      const p = await S.pickFolder("Choose where the Constellation library lives");
      if (p) { state.cfg.libraryRoot = p; document.getElementById("lib").value = p; showMath(); showTech(); }
    };
    document.getElementById("addSrc").onclick = async () => {
      const p = await S.pickFolder("Choose a photo folder");
      if (p) {
        const l = document.createElement("label");
        l.innerHTML = `<input type="checkbox" data-src="${esc(p)}" checked> ${esc(p)}`;
        document.getElementById("srcList").appendChild(l);
      }
    };
    document.querySelectorAll("[data-bk]").forEach((r) => {
      r.onchange = () => { state.cfg.backupTarget = r.getAttribute("data-bk"); showTech(); };
    });
    document.getElementById("pickBk").onclick = async () => {
      const p = await S.pickFolder("Choose the backup drive or folder");
      if (p) { state.cfg.backupTarget = p; showTech();
        document.getElementById("bkErr").textContent = `Backups will go to ${p}`; }
    };
    document.getElementById("upd").onchange = (e) => { state.cfg.updates = e.target.checked; showTech(); };

    nextBtn.textContent = "Download →";
    nextBtn.onclick = async () => {
      state.cfg.libraryRoot = document.getElementById("lib").value.trim();
      state.cfg.sources = [...document.querySelectorAll("[data-src]")]
        .filter((c) => c.checked).map((c) => c.getAttribute("data-src"));
      const fl = D.hardwareFloor(state.scan.sys, storage.drives, state.cfg.libraryRoot);
      const pinErr = D.validatePin(document.getElementById("pin1").value, document.getElementById("pin2").value);
      const bkErr = D.validateBackupTarget(state.cfg.backupTarget, state.cfg.libraryRoot, storage.drives, state.cfg.sources);
      document.getElementById("libErr").textContent = fl.ok ? "" : fl.problems.join(" ");
      document.getElementById("pinErr").textContent = pinErr || "";
      document.getElementById("bkErr").textContent = bkErr || "";
      if (!fl.ok || pinErr || bkErr) return;
      nextBtn.disabled = true;
      // The PIN goes straight to the backend, which hashes it. It is never
      // written to the config file, logs or this window's state.
      const r = await S.prepare({ ...state.cfg, pin: document.getElementById("pin1").value });
      document.getElementById("pin1").value = "";
      document.getElementById("pin2").value = "";
      nextBtn.disabled = false;
      if (!r.ok) {
        if (r.detail) log("prepare failed: " + r.detail);
        document.getElementById(r.field === "lib" ? "libErr" : "pinErr").textContent = r.error;
        return;
      }
      state.cfg.pinSet = true;
      log("prepare: library, family PIN and family certificate ready");
      go(3);
    };
  },

  // 4) Downloads -------------------------------------------------------------
  async download() {
    main.innerHTML = `
      <h2>Downloading</h2>
      <p class="sub">One-time downloads: the AI runtime and the model that reads your photos (about 6 GB).
        This is the only time setup uses the internet.</p>
      <div class="card">
        <div style="display:flex;justify-content:space-between"><span>AI runtime</span><span id="oMsg" class="muted">waiting…</span></div>
        <div class="track"><div class="fill" id="oFill"></div></div>
        <div style="display:flex;justify-content:space-between;margin-top:0.6rem"><span>Photo reader model</span><span id="mMsg" class="muted">waiting…</span></div>
        <div class="track"><div class="fill" id="mFill"></div></div>
      </div>
      <p class="err" id="dErr"></p>`;
    backBtn.style.visibility = "hidden";
    nextBtn.disabled = true;
    nextBtn.textContent = "Downloading…";
    const bar = (fill, msg, pr) => {
      const m = document.getElementById(msg); const f = document.getElementById(fill);
      if (!m || !f) return;
      m.textContent = pr.msg;
      if (pr.pct != null) { f.style.width = Math.round(pr.pct * 100) + "%"; if (pr.pct >= 1) f.classList.add("done"); }
    };
    S.onProgress((pr) => {
      if (pr.phase === "ollama") bar("oFill", "oMsg", pr);
      if (pr.phase === "model") bar("mFill", "mMsg", pr);
      if (pr.phase === "error") { const e = document.getElementById("dErr"); if (e) e.textContent = "⚠ " + pr.msg; }
    });
    const r = await S.install(state.cfg);
    if (r.ok) { nextBtn.disabled = false; nextBtn.textContent = "Bring in my photos →"; nextBtn.onclick = () => go(4); }
    else { document.getElementById("dErr").textContent = "⚠ " + (r.error || "download failed");
      nextBtn.textContent = "Try again"; nextBtn.disabled = false; nextBtn.onclick = () => go(3); }
  },

  // 5) First sweep — the patient sky (spec A7) ---------------------------------
  async sweep() {
    main.innerHTML = `
      <h2>Your sky is filling up</h2>
      <p class="sub">Reading your newest photos first so you can see them tonight.
        Setup will report whether older ones can continue unattended.</p>
      <div class="sky" id="sky" aria-hidden="true"></div>
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:baseline">
          <span id="swLabel">Starting…</span><span class="count" id="count"></span></div>
        <div class="track"><div class="fill" id="swFill"></div></div>
      </div>
      <p class="err" id="swErr"></p>`;
    backBtn.style.visibility = "hidden";
    nextBtn.disabled = true;
    nextBtn.textContent = "Working…";
    const sky = document.getElementById("sky");
    let stars = 0;
    const addStars = (n) => {
      for (let i = 0; i < n && stars < 400; i++, stars++) {
        const s = document.createElement("i");
        s.style.left = (Math.random() * 100) + "%";
        s.style.top = (Math.random() * 100) + "%";
        s.style.animationDelay = (Math.random() * 2.4) + "s";
        sky.appendChild(s);
      }
    };
    S.onProgress((pr) => {
      if (pr.phase !== "sweep") return;
      const lab = document.getElementById("swLabel");
      const f = document.getElementById("swFill");
      const c = document.getElementById("count");
      if (!lab || !f || !c) return;
      lab.textContent = String(pr.msg || "").split(" — ")[0];
      if (pr.count != null) {
        const before = Number(c.dataset.n || 0);
        c.dataset.n = pr.count;
        c.textContent = `${Number(pr.count).toLocaleString()} photos`;
        addStars(Math.max(0, Math.ceil((pr.count - before) / 5)));
      }
      if (pr.pct != null) { f.style.width = Math.round(pr.pct * 100) + "%"; if (pr.pct >= 1) f.classList.add("done"); }
    });
    const r = await S.sweep(state.cfg);
    if (r.ok) { nextBtn.disabled = false; nextBtn.textContent = "Finish →"; nextBtn.onclick = () => go(5); }
    else { document.getElementById("swErr").textContent = "⚠ " + (r.error || "something went wrong reading the photos");
      nextBtn.textContent = "Try again"; nextBtn.disabled = false; nextBtn.onclick = () => go(4); }
  },

  // 6) Finish ------------------------------------------------------------------
  async done() {
    main.innerHTML = `<div class="big"><div class="icon">✨</div>
      <h2 style="margin-top:0.6rem">Starting Constellation…</h2></div>`;
    backBtn.style.visibility = "hidden";
    nextBtn.style.display = "none";
    const r = await S.finish(state.cfg);
    if (!r.ok) {
      // Never a fake success: the server did not answer, so say what to do next.
      main.innerHTML = `<div class="big"><div class="icon">⚠</div>
        <h2>${esc(r.headline || "Constellation didn't start")}</h2>
        <p class="sub">${esc(r.error || r.autostartError || "Nothing is answering yet.")}</p>
        <p class="muted">${esc(r.retryHint || "Click Try again.")}</p></div>`;
      nextBtn.style.display = "";
      nextBtn.textContent = "Try again";
      nextBtn.onclick = () => go(5);
      return;
    }
    const u = D.finishUrls(await S.lanAddress());
    const copy = D.finishCopy(r);
    main.innerHTML = `<div class="big"><div class="icon">🌌</div>
      <h2 style="margin-top:0.6rem">${esc(r.headline || "You're all set")}</h2>
      <p class="sub" style="max-width:540px;margin:0.6rem auto 1rem">
        ${esc(copy.startup)}
        ${esc(copy.progress)}</p>
      ${!r.startsItself && r.autostartError ? `<p class="muted" style="max-width:540px;margin:0 auto 0.8rem">Start-on-boot couldn't be set up (${esc(r.autostartError)}).</p>` : ""}
      <button class="primary" id="openWall">Open the sky</button>
      <div class="card" style="text-align:left;max-width:540px;margin:1.4rem auto 0">
        <b>📺 On the TV</b>
        <p class="muted" style="margin:0.4rem 0 0">Open <code>${esc(u.tv)}</code> in the TV's browser, or in the Constellation app.
          No PIN needed — it's a picture frame.</p>
        <b style="display:block;margin-top:0.8rem">🔒 Private pages</b>
        <p class="muted" style="margin:0.4rem 0 0">Grown-ups open <code>${esc(u.app)}</code> and type the family PIN.
          The first time on each phone, install the family certificate from <code>${esc(u.ca)}</code>
          so the phone trusts this computer.</p>
      </div></div>`;
    document.getElementById("openWall").onclick = () => S.openUrl(u.wall);
  },
};

nextBtn.onclick = () => go(step + 1);
go(0);
