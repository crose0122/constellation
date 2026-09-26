// Real-binary E2E for the CP2 installer engine (no Electron window):
// prepare -> first sweep -> autostart (systemd user unit) -> server up ->
// wall over HTTP, private over HTTPS with the PIN set during prepare.
// Runs as a throwaway HOME so it can't touch the real install.
"use strict";
const fs = require("fs"), os = require("os"), path = require("path"), https = require("https"), http = require("http");
const { execFileSync } = require("child_process");

const ROOT = process.argv[2];                       // sandbox dir
const BACKEND = process.argv[3];                    // dist/memoryvault-brain
const PHOTOS = process.argv[4];                     // source photo folder
const PIN = "6284";
process.env.HOME = ROOT;                            // autostart writes under ~/.config
const setup = require(path.resolve(__dirname, "..", "setup.js"));
const autostart = require(path.resolve(__dirname, "..", "autostart.js"));

const results = [];
const check = (name, cond, extra = "") => { results.push(!!cond); console.log(`${cond ? "PASS" : "FAIL"} ${name}${extra ? "  " + extra : ""}`); };
const get = (mod, url, opts = {}) => new Promise((res) => {
  const r = mod.get(url, opts, (x) => { let b = ""; x.on("data", (c) => (b += c)); x.on("end", () => res({ status: x.statusCode, headers: x.headers, body: b })); });
  r.on("error", (e) => res({ status: 0, error: e.message })); r.setTimeout(8000, () => r.destroy());
});
const post = (url, body, opts = {}) => new Promise((res) => {
  const data = JSON.stringify(body);
  const r = https.request(url, { method: "POST", ...opts, headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) } },
    (x) => { let b = ""; x.on("data", (c) => (b += c)); x.on("end", () => res({ status: x.statusCode, headers: x.headers, body: b })); });
  r.on("error", (e) => res({ status: 0, error: e.message })); r.end(data);
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const data = path.join(ROOT, "Constellation");
  const lib = path.join(data, "library");
  const cfg = { libraryRoot: lib, model: "qwen2.5vl:7b", mode: "cpu", sources: [PHOTOS],
                backupTarget: path.join(ROOT, "elements"), updates: true, vaultMode: "dir", pin: PIN };

  const p = await setup.prepare(BACKEND, data, cfg);
  check("prepare: library + PIN + family cert", p.ok, p.error || "");
  check("PIN stored hashed, not in config", fs.existsSync(path.join(lib, ".auth", "pin.json")) &&
        !fs.readFileSync(p.envFile, "utf8").includes(PIN) &&
        !fs.readFileSync(path.join(lib, ".auth", "pin.json"), "utf8").includes(PIN));
  check("family CA created, keys 0600", fs.statSync(path.join(lib, ".tls", "ca.key")).mode % 512 === 0o600);

  const seen = [];
  const t0 = Date.now();
  const s = await setup.runFirstSweep(BACKEND, cfg, (x) => seen.push(x));
  const counts = seen.filter((x) => x.count != null).map((x) => x.count);
  check("first sweep ok", s.ok, `${((Date.now() - t0) / 1000).toFixed(1)}s`);
  // the overnight chain, run inline here so the wall can be checked
  const envBg = { ...process.env, ...Object.fromEntries(fs.readFileSync(p.envFile, "utf8").trim().split("\n").map((l) => l.split(/=(.*)/s).slice(0, 2))) };
  for (const st of [["screen"]]) execFileSync(path.join(BACKEND, "memoryvault-brain"), st, { env: envBg, stdio: "pipe" });
  const screened = execFileSync("sqlite3", ["-readonly", path.join(lib, "photos.db"), "select count(*) from photos where status='screened'"]).toString().trim();
  check("bundled model screens photos (no GPU, no torch)", Number(screened) > 0, `screened=${screened}`);
  check("patient sky counted up", counts.length > 0 && counts[counts.length - 1] > 0, `counts=${counts.slice(0, 3).join(",")}…${counts.at(-1)}`);

  // autostart: real systemd --user unit, but under a unique name so the
  // family's install is untouched
  const exe = setup.backendExe(BACKEND);
  const unitText = autostart.systemdUnit({ exe, envFile: p.envFile, httpPort: 28484, tlsPort: 28485 });
  const unitName = "constellation-e2e.service";
  const realHome = os.userInfo().homedir;
  const unitPath = path.join(realHome, ".config", "systemd", "user", unitName);
  fs.writeFileSync(unitPath, unitText);
  execFileSync("systemctl", ["--user", "daemon-reload"]);
  execFileSync("systemctl", ["--user", "start", unitName]);
  let up = false;
  for (let i = 0; i < 40 && !up; i++) { await sleep(500); up = (await get(http, "http://127.0.0.1:28484/wall")).status === 200; }
  check("systemd user unit starts the server", up);

  const wall = await get(http, "http://127.0.0.1:28484/api/gallery?limit=5");
  let n = 0; try { n = (JSON.parse(wall.body).photos || []).length; } catch { /* */ }
  check("wall shows photos with no PIN", wall.status === 200 && n > 0, `photos=${n}`);

  const ca = fs.readFileSync(path.join(lib, ".tls", "ca.pem"));
  const agent = new https.Agent({ ca, servername: "localhost" });
  const priv = await get(https, "https://localhost:28485/api/curation", { agent });
  check("private page over HTTPS needs the PIN", priv.status === 401);
  const login = await post("https://localhost:28485/api/auth/login", { pin: PIN }, { agent });
  const cookie = (login.headers["set-cookie"] || []).map((c) => c.split(";")[0]).join("; ");
  check("PIN from the wizard logs in over HTTPS", login.status === 200);
  const priv2 = await get(https, "https://localhost:28485/api/curation", { agent, headers: { Cookie: cookie } });
  check("private page opens after PIN", priv2.status === 200);

  execFileSync("systemctl", ["--user", "kill", "-s", "KILL", unitName]);
  let back = false;
  for (let i = 0; i < 40 && !back; i++) { await sleep(500); back = (await get(http, "http://127.0.0.1:28484/wall")).status === 200; }
  check("server comes back after being killed (Restart=always)", back);

  execFileSync("systemctl", ["--user", "stop", unitName]);
  fs.unlinkSync(unitPath);
  execFileSync("systemctl", ["--user", "daemon-reload"]);
  console.log(`${results.filter(Boolean).length}/${results.length} passed`);
  process.exit(results.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error("E2E crashed:", e); process.exit(2); });
