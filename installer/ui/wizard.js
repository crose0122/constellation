// Constellation Setup — wizard flow (renderer).
"use strict";
const S = window.setup;                 // preload bridge
const main = document.getElementById("main");
const stepsEl = document.getElementById("steps");
const backBtn = document.getElementById("back");
const nextBtn = document.getElementById("next");

const state = { scan: null, cfg: { model: "qwen2.5vl:7b", libraryRoot: "", sources: [], vaultMode: "dir" } };
const STEPS = ["welcome", "scan", "storage", "download", "sweep", "done"];
let step = 0;

function gb(n) { return (n == null) ? "?" : `${n} GB`; }
function esc(s) { return String(s || "").replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c])); }

function renderSteps() {
  stepsEl.innerHTML = STEPS.map((_, i) =>
    `<div class="s ${i < step ? "done" : i === step ? "on" : ""}"></div>`).join("");
}

async function go(n) { step = Math.max(0, Math.min(STEPS.length - 1, n)); renderSteps(); await render(); }
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
        <p class="sub" style="max-width:520px;margin:0.6rem auto 0">
          Your family's photos and videos, organized by a private AI that runs
          entirely on <b>this computer</b> — nothing ever leaves your network.
          This setup will check your hardware, pick the right AI model for your
          graphics card, download it, and get you running.</p>
      </div>`;
    nextBtn.textContent = "Get started →";
    nextBtn.onclick = () => go(1);
  },

  // 2) System scan -----------------------------------------------------------
  async scan() {
    main.innerHTML = `<h2>Checking your computer…</h2>
      <p class="sub">Looking at your graphics card, memory, storage, and network.</p>
      <div class="spin"></div>`;
    nextBtn.disabled = true;
    const r = await S.scan();
    if (!r.ok) { main.innerHTML = `<h2>Scan failed</h2><p class="sub">${esc(r.error)}</p>`; return; }
    state.scan = r.data;
    const { sys, gpu, storage, network, recommendation: rec } = r.data;
    if (!state.cfg.libraryRoot) state.cfg.libraryRoot = (await S.defaults()).libraryRoot;
    state.cfg.model = rec.model;

    const gpuLine = gpu.name
      ? `${esc(gpu.name)}${gpu.vramGB ? " · " + gb(gpu.vramGB) + " VRAM" : ""}`
      : "No dedicated GPU found";
    main.innerHTML = `
      <h2>Here's what I found</h2>
      <p class="sub">Review your hardware and the recommended AI model, then continue.</p>
      <div class="card">
        <div class="row"><span class="k">Graphics card</span><span class="v">${gpuLine}</span></div>
        <div class="row"><span class="k">Processor</span><span class="v">${esc(sys.cpu)} · ${sys.cores} cores</span></div>
        <div class="row"><span class="k">Memory</span><span class="v">${gb(sys.ramGB)}</span></div>
        <div class="row"><span class="k">Drives</span><span class="v">${storage.drives.length} · ${network.machines.length} other computers on the network</span></div>
      </div>
      <div class="card rec ${rec.ok && rec.speed !== "slow" && rec.mode !== "gpu-offload" ? "" : "warn"}">
        <div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.5rem">
          <span class="badge ${rec.speed === "slow" || rec.mode === "gpu-offload" ? "warn" : "ok"}">
            ${rec.speed === "slow" ? "will be slow" : rec.mode === "gpu-offload" ? "workable" : "recommended"}</span>
          <b>${esc(rec.model)}</b>
        </div>
        <p class="muted">${esc(rec.note)}</p>
      </div>`;
    if (gpu.detail) main.innerHTML += `<p class="muted">⚠ ${esc(gpu.detail)}</p>`;
    state.cfg.mode = rec.mode;
    // manual override — when more than one path is viable, let the user choose
    if (rec.modes && rec.modes.length > 1) {
      const MODE_LABEL = { gpu: "Use my graphics card (faster)", cpu: "Use the CPU only (slower, always works)" };
      main.innerHTML += `
        <div class="card"><div class="row" style="align-items:center">
          <span class="k">Run the AI on</span>
          <select id="modeSel" style="background:#06101c;border:1px solid #1c3e60;color:#dfeefa;border-radius:8px;padding:0.4rem 0.6rem;font:inherit">
            ${rec.modes.map((m) => `<option value="${m}" ${(/gpu/.test(rec.mode) ? m === "gpu" : m === rec.mode) ? "selected" : ""}>${MODE_LABEL[m] || m}</option>`).join("")}
          </select>
        </div><p class="muted" style="margin-top:0.4rem">We picked the best option for your hardware — change it only if you hit trouble.</p></div>`;
      setTimeout(() => {
        const sel = document.getElementById("modeSel");
        if (sel) sel.onchange = () => { state.cfg.mode = sel.value === "gpu" && /offload/.test(rec.mode) ? "gpu-offload" : sel.value; };
      }, 0);
    }
    nextBtn.disabled = false;
    nextBtn.onclick = () => go(2);
  },

  // 3) Storage + sources -----------------------------------------------------
  async storage() {
    const { storage } = state.scan;
    const cands = storage.photoCandidates;
    main.innerHTML = `
      <h2>Where are your photos?</h2>
      <p class="sub">Pick where Constellation keeps its library, and which folders to pull photos & videos from. Sources are only ever read, never changed.</p>
      <h3 style="font-size:0.82rem;color:#7f9bb3;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.4rem">Library location</h3>
      <div class="card"><div class="pathrow">
        <input id="lib" value="${esc(state.cfg.libraryRoot)}">
        <button class="ghost" id="pickLib">Change…</button>
      </div><p class="muted" style="margin-top:0.4rem">Managed library + database live here. Needs room for your collection.</p></div>
      <h3 style="font-size:0.82rem;color:#7f9bb3;text-transform:uppercase;letter-spacing:0.1em;margin:0.9rem 0 0.4rem">Photo sources</h3>
      <div class="card list" id="srcList">
        ${cands.length ? cands.map((c, i) =>
          `<label><input type="checkbox" data-src="${esc(c)}" ${i === 0 ? "checked" : ""}> ${esc(c)}</label>`).join("")
          : `<p class="muted">No obvious photo folders found — add one below.</p>`}
      </div>
      <button class="ghost" id="addSrc">+ Add a folder…</button>`;
    document.getElementById("pickLib").onclick = async () => {
      const p = await S.pickFolder("Choose the Constellation library location");
      if (p) { state.cfg.libraryRoot = p; document.getElementById("lib").value = p; }
    };
    document.getElementById("addSrc").onclick = async () => {
      const p = await S.pickFolder("Choose a photo/video folder");
      if (p) {
        const l = document.createElement("label");
        l.innerHTML = `<input type="checkbox" data-src="${esc(p)}" checked> ${esc(p)}`;
        document.getElementById("srcList").appendChild(l);
      }
    };
    nextBtn.textContent = "Download AI →";
    nextBtn.onclick = () => {
      state.cfg.libraryRoot = document.getElementById("lib").value.trim();
      state.cfg.sources = [...document.querySelectorAll("[data-src]")]
        .filter((c) => c.checked).map((c) => c.getAttribute("data-src"));
      go(3);
    };
  },

  // 4) Download AI -----------------------------------------------------------
  async download() {
    const rec = state.scan.recommendation;
    main.innerHTML = `
      <h2>Downloading the AI</h2>
      <p class="sub">Installing Ollama and pulling <b>${esc(state.cfg.model)}</b> (~${rec.downloadGB} GB) onto your graphics card. This is a one-time download.</p>
      <div class="card">
        <div style="display:flex;justify-content:space-between"><span>Ollama runtime</span><span id="oMsg" class="muted">waiting…</span></div>
        <div class="track"><div class="fill" id="oFill"></div></div>
        <div style="display:flex;justify-content:space-between;margin-top:0.6rem"><span>Vision model</span><span id="mMsg" class="muted">waiting…</span></div>
        <div class="track"><div class="fill" id="mFill"></div></div>
      </div>
      <p class="muted" id="dErr"></p>`;
    backBtn.style.visibility = "hidden";
    nextBtn.disabled = true;
    nextBtn.textContent = "Downloading…";
    const set = (fill, msg, p) => {
      const f = document.getElementById(fill); const m = document.getElementById(msg);
      if (p != null) { f.style.width = Math.round(p * 100) + "%"; if (p >= 1) f.classList.add("done"); }
      if (m) m.textContent = "";
    };
    S.onProgress((pr) => {
      if (pr.phase === "ollama") { document.getElementById("oMsg").textContent = pr.msg; if (pr.pct != null) { document.getElementById("oFill").style.width = Math.round(pr.pct * 100) + "%"; if (pr.pct >= 1) document.getElementById("oFill").classList.add("done"); } }
      if (pr.phase === "model") { document.getElementById("mMsg").textContent = pr.msg; if (pr.pct != null) { document.getElementById("mFill").style.width = Math.round(pr.pct * 100) + "%"; if (pr.pct >= 1) document.getElementById("mFill").classList.add("done"); } }
      if (pr.phase === "error") document.getElementById("dErr").textContent = "⚠ " + pr.msg;
    });
    const r = await S.install(state.cfg);
    if (r.ok) { nextBtn.disabled = false; nextBtn.textContent = "Add my photos →"; nextBtn.onclick = () => go(4); }
    else { document.getElementById("dErr").textContent = "⚠ " + (r.error || "download failed"); nextBtn.textContent = "Retry"; nextBtn.disabled = false; nextBtn.onclick = () => go(3); }
  },

  // 5) First sweep -----------------------------------------------------------
  // Read the chosen folders and actually populate the library. Everything here
  // is hashing and EXIF — no model — so it finishes in minutes and the app
  // opens with real photos in it. The model-bound stages start afterwards, in
  // the background, and report themselves on the app's own progress page.
  async sweep() {
    const n = (state.cfg.sources || []).length;
    main.innerHTML = `
      <h2>Adding your photos</h2>
      <p class="sub">Reading ${n === 1 ? "your folder" : `your ${n} folders`} and building the
        library. Originals are only ever read, never moved or changed.</p>
      <div class="card">
        <div style="display:flex;justify-content:space-between">
          <span id="swLabel">Starting…</span></div>
        <div class="track"><div class="fill" id="swFill"></div></div>
        <p class="muted" id="swLine" style="margin:0.7rem 0 0;font-size:0.85rem"></p>
      </div>
      <p class="muted" id="swErr"></p>`;
    backBtn.style.visibility = "hidden";
    nextBtn.disabled = true;
    nextBtn.textContent = "Working…";
    // onProgress registers an ipcRenderer listener that outlives this view, so
    // a retry leaves an older handler pointing at DOM that no longer exists.
    // Every lookup is guarded rather than assumed.
    S.onProgress((pr) => {
      if (pr.phase !== "sweep") return;
      const [label, detail] = String(pr.msg || "").split(" — ");
      const lab = document.getElementById("swLabel");
      const line = document.getElementById("swLine");
      const f = document.getElementById("swFill");
      if (!lab || !line || !f) return;      // this view is gone; nothing to draw
      lab.textContent = label || "";
      line.textContent = detail || "";
      if (pr.pct != null) {
        f.style.width = Math.round(pr.pct * 100) + "%";
        if (pr.pct >= 1) f.classList.add("done");
      }
    });
    const r = await S.sweep(state.cfg);
    if (r.ok) {
      nextBtn.disabled = false;
      nextBtn.textContent = "Finish →";
      nextBtn.onclick = () => go(5);
    } else {
      document.getElementById("swErr").textContent = "⚠ " + (r.error || "sweep failed");
      nextBtn.textContent = "Retry";
      nextBtn.disabled = false;
      nextBtn.onclick = () => go(4);
    }
  },

  // 5) Done ------------------------------------------------------------------
  async done() {
    main.innerHTML = `<div class="big"><div class="icon">✨</div>
      <h2 style="margin-top:0.6rem">Starting Constellation…</h2>
      <p class="sub" id="doneMsg">Configuring and launching.</p></div>`;
    backBtn.style.visibility = "hidden";
    nextBtn.disabled = true; nextBtn.style.display = "none";
    const r = await S.finish(state.cfg);
    if (r.ok) {
      const lan = await S.lanAddress();
      const tvUrl = lan ? `http://${lan}:8484/?lite=1` : null;
      main.innerHTML = `<div class="big"><div class="icon">🌌</div>
        <h2 style="margin-top:0.6rem">You're all set!</h2>
        <p class="sub" style="max-width:520px;margin:0.6rem auto 1rem">
          Your photos are in and Constellation is running at
          <code>${esc(r.url)}</code>.${r.background ? `
          The AI is tagging them, recognising faces and finding places in the
          background — the app's <b>Progress</b> page shows it filling in, and
          it keeps going after you close this window.` : ""}</p>
        <button class="primary" id="openApp">Open Constellation</button>
        <div class="card" style="text-align:left;max-width:520px;margin:1.4rem auto 0">
          <b>📺 Put it on your TV</b>
          <p class="muted" style="margin:0.45rem 0 0;font-size:0.88rem">
            ${tvUrl ? `On an Android TV box or tablet on this network, install the
            Constellation app and enter <code>${esc(tvUrl)}</code> — or just open
            that address in the TV's browser. It can run as an always-on photo
            frame or as the system screensaver.`
            : `Connect this computer to your network, then open Constellation's
            address on the TV to use it as an always-on photo frame.`}
          </p>
        </div>
        <p class="muted" style="margin-top:1.1rem;font-size:0.86rem">
          Next: open <b>People</b> and name a face once — every photo of that
          person becomes searchable.</p>
        </div>`;
      document.getElementById("openApp").onclick = () => S.openUrl(r.url);
    } else {
      main.innerHTML = `<div class="big"><div class="icon">⚠</div>
        <h2>Couldn't start automatically</h2><p class="sub">${esc(r.error)}</p></div>`;
    }
  },
};

nextBtn.onclick = () => go(step + 1);
go(0);
