/* Constellation global way-home control (task #388).
 *
 * The server injects this into every HTML page it serves, so no page (a deep
 * link, Curation, Progress, a person page) can leave someone stuck with no
 * way back. Two small buttons, top-left: "‹ Back" (only when we arrived from
 * another Constellation page) and "☰ Menu" (hidden on the menu itself).
 *
 * Picture-frame surfaces (/, /ambient, /memories, /wall) must stay clean on a
 * TV, so there the control fades out after a few idle seconds and returns on
 * any mouse move, touch or key press. Elsewhere it stays visible.
 *
 * Built with DOM APIs and textContent only — no HTML strings.
 */
(function () {
  "use strict";
  if (window.__cstNav) return;
  window.__cstNav = true;

  var path = location.pathname;
  var onMenu = path === "/menu" || path === "/home";
  var frame = path === "/" || path === "/ambient" || path === "/memories" || path === "/wall";
  var IDLE_MS = 4000;

  var CSS =
    "#cst-nav{position:fixed;top:10px;left:10px;z-index:2147482000;display:flex;gap:6px;" +
    "transition:opacity .4s;font:600 14px/1 -apple-system,'Segoe UI',Roboto,sans-serif}" +
    "#cst-nav.idle{opacity:0;pointer-events:none}" +
    "#cst-nav a,#cst-nav button{display:inline-flex;align-items:center;gap:4px;padding:8px 12px;" +
    "border-radius:999px;border:1px solid rgba(148,163,184,.45);background:rgba(15,23,42,.78);" +
    "color:#f8fafc;text-decoration:none;cursor:pointer;font:inherit;backdrop-filter:blur(4px)}" +
    "#cst-nav a:hover,#cst-nav button:hover,#cst-nav a:focus-visible,#cst-nav button:focus-visible" +
    "{background:rgba(30,41,59,.95);outline:2px solid #fbbf24;outline-offset:1px}";

  function sameOriginReferrer() {
    try { return document.referrer && new URL(document.referrer).origin === location.origin; }
    catch (e) { return false; }
  }

  function build() {
    if (document.getElementById("cst-nav")) return;
    var style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    var bar = document.createElement("nav");
    bar.id = "cst-nav";
    bar.setAttribute("aria-label", "Constellation navigation");

    if (sameOriginReferrer()) {   // came from another Constellation page
      var back = document.createElement("button");
      back.type = "button";
      back.id = "cst-nav-back";
      back.textContent = "\u2039 Back";
      back.addEventListener("click", function () { history.back(); });
      bar.appendChild(back);
    }
    // a page that already shows its own Menu link (index, or the chips from
    // PR #15) keeps it; never two Menu buttons on one screen
    var ownMenu = document.querySelector('a[href="/menu"]:not(#cst-nav-menu)');
    if (!onMenu && !ownMenu) {
      var menu = document.createElement("a");
      menu.id = "cst-nav-menu";
      menu.href = "/menu";
      menu.textContent = "\u2630 Menu";
      bar.appendChild(menu);
    }
    if (!bar.firstChild) return;   // the menu page with no history: nothing to show
    document.body.appendChild(bar);

    if (frame) {
      var t = null;
      var wake = function () {
        bar.classList.remove("idle");
        clearTimeout(t);
        t = setTimeout(function () { bar.classList.add("idle"); }, IDLE_MS);
      };
      ["mousemove", "pointerdown", "touchstart", "keydown"].forEach(function (ev) {
        window.addEventListener(ev, wake, { passive: true });
      });
      wake();
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();
