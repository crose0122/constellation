/* Constellation Debug Console (fleet debug-console contract v1).
 *
 * Floating, bottom-right, dark. Ctrl+Shift+D anywhere toggles it; the Home
 * page's "Debug Console" card and the `constellation:toggle-debug` event do
 * too. While open it polls GET /api/debug/state every 2 s, tailing with
 * ?since=<last t>, and shows status chips + the server's own log lines.
 *
 * /api/debug/state is behind the family PIN (and HTTPS). The poll uses
 * XMLHttpRequest, NOT fetch: pin.js wraps fetch and sends the page to /login
 * on any 401, and a picture frame must never be navigated away because
 * someone pressed a debug shortcut on it.
 *
 * Everything shown is inserted as text (textContent), never as HTML.
 */
(function () {
  "use strict";
  if (window.__cstDebugConsole) return;
  window.__cstDebugConsole = true;

  var POLL_MS = 2000, MAX_LINES = 800;
  var open = false, paused = false, timer = null, lastT = null, busy = false;
  var seen = {}, lines = [];
  var root, chipsEl, logEl, errEl, pauseBtn;

  var CSS =
    "#cst-debug{position:fixed;right:12px;bottom:12px;z-index:2147483000;" +
    "width:min(680px,95vw);height:min(460px,70vh);display:flex;flex-direction:column;" +
    "background:rgba(2,6,23,.96);color:#e2e8f0;border:1px solid #334155;border-radius:12px;" +
    "box-shadow:0 20px 50px rgba(0,0,0,.6);font:12px/1.4 -apple-system,'Segoe UI',Roboto,sans-serif;" +
    "backdrop-filter:blur(6px)}" +
    "#cst-debug .hd{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;" +
    "border-bottom:1px solid #1e293b}" +
    "#cst-debug .ttl{font-weight:700;color:#c4b5fd;font-size:12px}" +
    "#cst-debug .ttl small{color:#64748b;font-weight:400;font-size:9px;margin-left:6px}" +
    "#cst-debug .hd button{font:inherit;font-size:10px;color:#e2e8f0;background:#1e293b;border:0;" +
    "border-radius:5px;padding:2px 8px;margin-left:6px;cursor:pointer}" +
    "#cst-debug .hd button:hover{background:#334155}" +
    "#cst-debug .chips{display:flex;flex-wrap:wrap;gap:6px;padding:8px 12px;border-bottom:1px solid rgba(30,41,59,.7)}" +
    "#cst-debug .chip{padding:3px 8px;border-radius:5px;font-size:10px;font-weight:600;cursor:default;" +
    "background:#334155;color:#cbd5e1;border:1px solid #475569;max-width:100%;white-space:nowrap;" +
    "overflow:hidden;text-overflow:ellipsis}" +
    "#cst-debug .chip.ok{background:rgba(5,150,105,.25);color:#6ee7b7;border-color:rgba(16,185,129,.4)}" +
    "#cst-debug .chip.bad{background:rgba(225,29,72,.22);color:#fda4af;border-color:rgba(244,63,94,.4)}" +
    "#cst-debug .log{flex:1;overflow-y:auto;padding:8px 12px;" +
    "font:10.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-word}" +
    "#cst-debug .ln{color:#cbd5e1}#cst-debug .ln.warn{color:#fcd34d}#cst-debug .ln.error{color:#fda4af}" +
    "#cst-debug .ln .t{color:#475569;margin-right:6px}" +
    "#cst-debug .err{color:#fb7185;padding:0 0 4px}#cst-debug .err a{color:#93c5fd}" +
    "#cst-debug .empty{color:#64748b}";

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function build() {
    var st = el("style"); st.textContent = CSS; document.head.appendChild(st);
    root = el("div"); root.id = "cst-debug"; root.setAttribute("role", "dialog");
    root.setAttribute("aria-label", "Debug Console");
    var hd = el("div", "hd");
    var ttl = el("div", "ttl", "\u{1F41E} Debug Console");
    ttl.appendChild(el("small", null, "Ctrl+Shift+D"));
    var btns = el("div");
    pauseBtn = el("button", null, "Pause");
    pauseBtn.onclick = function () { paused = !paused; pauseBtn.textContent = paused ? "Resume" : "Pause"; schedule(); };
    var clr = el("button", null, "Clear");
    clr.onclick = function () { lines = []; seen = {}; renderLog(); };
    var ref = el("button", null, "Refresh"); ref.onclick = function () { poll(); };
    var cls = el("button", null, "Close"); cls.onclick = function () { toggle(false); };
    [pauseBtn, clr, ref, cls].forEach(function (b) { btns.appendChild(b); });
    hd.appendChild(ttl); hd.appendChild(btns);
    chipsEl = el("div", "chips");
    logEl = el("div", "log");
    errEl = el("div", "err");
    root.appendChild(hd); root.appendChild(chipsEl); root.appendChild(logEl);
    document.body.appendChild(root);
    renderLog();
  }

  function renderChips(checks) {
    chipsEl.textContent = "";
    (checks || []).forEach(function (c) {
      var cls = c.ok === true ? "chip ok" : c.ok === false ? "chip bad" : "chip";
      var d = String(c.detail || "");
      var chip = el("div", cls, c.label + (d ? ": " + (d.length > 42 ? d.slice(0, 41) + "\u2026" : d) : ""));
      chip.title = c.label + " \u2014 " + d;
      chipsEl.appendChild(chip);
    });
  }

  function renderLog() {
    var stick = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
    logEl.textContent = "";
    if (errEl.textContent || errEl.childNodes.length) logEl.appendChild(errEl);
    if (!lines.length && !errEl.childNodes.length) logEl.appendChild(el("div", "empty", "Waiting for log lines\u2026"));
    lines.forEach(function (e) {
      var row = el("div", "ln " + (e.level === "error" ? "error" : e.level === "warn" ? "warn" : ""));
      row.appendChild(el("span", "t", String(e.t || "").slice(11, 19)));
      row.appendChild(document.createTextNode(e.msg));
      logEl.appendChild(row);
    });
    if (stick || !paused) logEl.scrollTop = logEl.scrollHeight;
  }

  function setErr(msg, linkHref, linkText) {
    errEl.textContent = msg || "";
    if (linkHref) {
      errEl.appendChild(document.createTextNode(" "));
      var a = el("a", null, linkText); a.href = linkHref; errEl.appendChild(a);
    }
  }

  function poll() {
    if (busy) return;
    busy = true;
    var xhr = new XMLHttpRequest();
    var url = "/api/debug/state" + (lastT ? "?since=" + encodeURIComponent(lastT) : "");
    xhr.open("GET", url, true);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.timeout = 6000;
    xhr.onloadend = function () {
      busy = false;
      var d = null;
      try { d = JSON.parse(xhr.responseText || "null"); } catch (e) { d = null; }
      if (xhr.status === 200 && d) {
        setErr("");
        renderChips(d.checks);
        var fresh = (d.log || []).filter(function (e) {
          var k = e.t + "\u0000" + e.msg;
          if (seen[k]) return false;
          seen[k] = 1; return true;
        });
        lines = lines.concat(fresh);
        if (lines.length > MAX_LINES) {
          lines.splice(0, lines.length - MAX_LINES);
        }
        if (d.log && d.log.length) lastT = d.log[d.log.length - 1].t;
        renderLog();
      } else if (xhr.status === 401) {
        setErr("The debug console needs the family PIN.",
               "/login?next=" + encodeURIComponent(location.pathname + location.search), "Sign in");
        renderLog();
      } else if (xhr.status === 403 && d && d.https) {
        setErr("Open the private (https) address to use the debug console.", d.https, "Go there");
        renderLog();
      } else {
        setErr(xhr.status ? "HTTP " + xhr.status + (d && d.error ? " \u2014 " + d.error : "") : "server unreachable");
        renderLog();
      }
    };
    xhr.send();
  }

  function schedule() {
    if (timer) { clearInterval(timer); timer = null; }
    if (open && !paused) { poll(); timer = setInterval(poll, POLL_MS); }
  }

  function toggle(force) {
    open = typeof force === "boolean" ? force : !open;
    if (open && !root) build();
    if (root) root.style.display = open ? "flex" : "none";
    schedule();
  }

  window.addEventListener("keydown", function (e) {
    if (e.ctrlKey && e.shiftKey && (e.key === "D" || e.key === "d" || e.code === "KeyD")) {
      e.preventDefault(); e.stopPropagation();
      toggle();
    }
  }, true);
  window.addEventListener("constellation:toggle-debug", function () { toggle(); });
  window.cstToggleDebug = toggle;
  if (/[?&]debug=1\b/.test(location.search)) {
    if (document.body) toggle(true);
    else document.addEventListener("DOMContentLoaded", function () { toggle(true); });
  }
})();
