// Family-PIN glue for every Constellation page (V2 CP1).
// Wraps window.fetch so same-origin requests carry the CSRF token, and sends
// the family to /login when a private action needs the PIN. Display pages keep
// working with no PIN: only private endpoints ever answer 401.
(function () {
  "use strict";
  var nativeFetch = window.fetch.bind(window);

  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)mv_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function sameOrigin(input) {
    try {
      var u = new URL(typeof input === "string" ? input : input.url, location.href);
      return u.origin === location.origin;
    } catch (e) { return false; }
  }

  window.fetch = function (input, init) {
    init = init || {};
    if (sameOrigin(input)) {
      var h = new Headers(init.headers || (typeof input !== "string" && input.headers) || {});
      var t = csrf();
      if (t && !h.has("X-Constellation-CSRF")) h.set("X-Constellation-CSRF", t);
      init.headers = h;
      init.credentials = init.credentials || "same-origin";
    }
    return nativeFetch(input, init).then(function (res) {
      if (res.status === 401 && sameOrigin(input)) {
        var next = encodeURIComponent(location.pathname + location.search);
        location.href = "/login?next=" + next;
      }
      return res;
    });
  };

  // Named vault folders come from the server's settings (household-specific,
  // PIN-gated). pickVaultFolder() asks one folder at a time; null = cancelled.
  var foldersP = null;
  window.vaultFolders = function () {
    if (!foldersP) foldersP = window.fetch("/api/vault/folders")
      .then(function (r) { return r.ok ? r.json() : { folders: [] }; })
      .then(function (j) { return j.folders || []; })
      .catch(function () { return []; });
    return foldersP;
  };
  window.pickVaultFolder = function (intro) {
    return window.vaultFolders().then(function (fs) {
      for (var i = 0; i < fs.length; i++) {
        var name = fs[i].charAt(0).toUpperCase() + fs[i].slice(1);
        if (confirm((i === 0 ? intro + "\n\n" : "") + "OK = the " + name + " folder" +
                    (i < fs.length - 1 ? " · Cancel = choose another" : ""))) return fs[i];
      }
      return null;
    });
  };
})();
