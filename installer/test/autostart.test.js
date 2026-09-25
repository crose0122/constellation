"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const a = require("../autostart");

test("systemd user unit: always restarts, no start limit, reads the config file", () => {
  const u = a.systemdUnit({ exe: "/opt/constellation/backend/memoryvault-brain", envFile: "/home/x/Constellation/.env" });
  assert.match(u, /^Restart=always$/m);
  assert.match(u, /^StartLimitIntervalSec=0$/m);
  assert.match(u, /^EnvironmentFile=\/home\/x\/Constellation\/\.env$/m);
  assert.match(u, /^ExecStart="\/opt\/constellation\/backend\/memoryvault-brain" constellation --host 0.0.0.0 --port 8484 --tls-port 8485$/m);
  assert.match(u, /^WantedBy=default\.target$/m);
  assert.match(u, /^NoNewPrivileges=yes$/m);
  assert.doesNotMatch(u, /PIN|pin=/i, "no secrets in the unit");
});

test("systemd unit quotes paths with spaces and refuses relative paths", () => {
  const u = a.systemdUnit({ exe: "/opt/My Photos/brain", envFile: "/h/.env" });
  assert.match(u, /ExecStart="\/opt\/My Photos\/brain" constellation/);
  assert.throws(() => a.systemdUnit({ exe: "brain", envFile: "/h/.env" }), /absolute/);
});

test("Windows task: logon trigger, least privilege, restart forever, no time limit", () => {
  const x = a.windowsTaskXml({ launcher: "C:\\Users\\x\\Constellation\\start-constellation.cmd", user: "HOME\\x" });
  assert.match(x, /<LogonTrigger>/);
  assert.match(x, /<RunLevel>LeastPrivilege<\/RunLevel>/);
  assert.match(x, /<ExecutionTimeLimit>PT0S<\/ExecutionTimeLimit>/);
  assert.match(x, /<Count>999<\/Count>/);
  assert.match(x, /<Command>C:\\Users\\x\\Constellation\\start-constellation\.cmd<\/Command>/);
});

test("Windows task XML-escapes the user and path", () => {
  const x = a.windowsTaskXml({ launcher: "C:\\A&B\\s.cmd", user: "D<\\u>" });
  assert.match(x, /C:\\A&amp;B/);
  assert.match(x, /D&lt;\\u&gt;/);
});

test("Windows launcher sets env and refuses cmd metacharacters instead of mis-escaping", () => {
  const l = a.windowsLauncher({ exe: "C:\\Program Files\\Constellation\\backend\\memoryvault-brain.exe",
    env: { MEMORYVAULT_LIBRARY_ROOT: "D:\\Constellation\\library" } });
  assert.match(l, /^set "MEMORYVAULT_LIBRARY_ROOT=D:\\Constellation\\library"\r$/m);
  assert.match(l, /"C:\\Program Files\\Constellation\\backend\\memoryvault-brain\.exe" constellation --host 0\.0\.0\.0 --port 8484 --tls-port 8485/);
  assert.throws(() => a.windowsLauncher({ exe: "C:\\x.exe", env: { MEMORYVAULT_LIBRARY_ROOT: "D:\\A&B" } }), /character/);
  assert.throws(() => a.windowsLauncher({ exe: "C:\\x.exe", env: { MEMORYVAULT_LIBRARY_ROOT: "D:\\100%" } }), /character/);
  assert.throws(() => a.windowsLauncher({ exe: "C:\\x.exe", env: { "bad name": "x" } }), /setting name/);
});

test("env file parser", () => {
  assert.deepEqual(a.parseEnvFile("A=1\r\nB=two words\n# no\nlower=x\n"), { A: "1", B: "two words" });
});

test("never overwrites a hand-made unit; may update its own", () => {
  const ours = a.systemdUnit({ exe: "/opt/c/brain", envFile: "/h/.env" });
  assert.ok(ours.startsWith(a.MARKER));
  assert.equal(a.mayWriteUnit(null), true);
  assert.equal(a.mayWriteUnit(ours), true);
  assert.equal(a.mayWriteUnit("[Unit]\nDescription=hand made\n"), false);
});

test("install refuses to clobber an existing hand-made unit (real file)", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  if (process.platform !== "linux") return;
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-home-"));
  const dir = path.join(home, ".config", "systemd", "user");
  fs.mkdirSync(dir, { recursive: true });
  const unit = path.join(dir, a.UNIT);
  fs.writeFileSync(unit, "[Service]\nExecStart=/the/family/working/install\n");
  const saved = process.env.HOME;
  process.env.HOME = home;
  try {
    const r = await a.install({ exe: "/opt/c/brain", envFile: "/h/.env", dataDir: home });
    assert.equal(r.ok, false);
    assert.match(r.error, /set up by hand/);
    assert.match(fs.readFileSync(unit, "utf8"), /the\/family\/working\/install/, "untouched");
  } finally { process.env.HOME = saved; }
});

// --- task #130: installer startup truthfulness ------------------------------
// Every rule below was added because the installer could claim a startup it
// had not verified. Each test has a matching mutation check in the evidence
// doc: break the rule, the suite goes red.

function sectionOf(unit, name) {
  // minimal systemd unit parser: returns the lines of one [Section]
  const lines = String(unit).split("\n");
  const start = lines.findIndex((l) => l.trim() === `[${name}]`);
  if (start < 0) return null;
  const body = [];
  for (let i = start + 1; i < lines.length; i++) {
    if (/^\s*\[/.test(lines[i])) break;
    body.push(lines[i]);
  }
  return body;
}

test("StartLimitIntervalSec is a [Unit] key — the generated unit puts it there, not in [Service]", () => {
  const u = a.systemdUnit({ exe: "/opt/c/brain", envFile: "/h/.env" });
  const unitBody = sectionOf(u, "Unit");
  const serviceBody = sectionOf(u, "Service");
  assert.ok(unitBody.some((l) => l.startsWith("StartLimitIntervalSec=")),
    "StartLimitIntervalSec must be inside [Unit] — systemd ignores it in [Service]");
  assert.ok(!serviceBody.some((l) => l.startsWith("StartLimitIntervalSec=")),
    "no StartLimitIntervalSec left in [Service]");
  assert.ok(serviceBody.some((l) => l.startsWith("Restart=always")), "Restart=always stays in [Service]");
});

test("the generated unit passes systemd-analyze verify with no warnings (real binary)", () => {
  if (process.platform !== "linux") return;
  const fs = require("fs"), os = require("os"), path = require("path");
  const { execFileSync } = require("child_process");
  let bin = "/usr/bin/systemd-analyze";
  if (!fs.existsSync(bin)) bin = "/bin/systemd-analyze";
  if (!fs.existsSync(bin)) return; // no systemd on this box; structural test above still holds
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-verify-"));
  const exe = path.join(dir, "brain");
  fs.writeFileSync(exe, "#!/bin/sh\nexit 0\n");
  fs.chmodSync(exe, 0o755);
  const unit = path.join(dir, "constellation-verify.service");
  fs.writeFileSync(unit, a.systemdUnit({ exe, envFile: path.join(dir, ".env") }));
  let stderr = "";
  try {
    execFileSync(bin, ["verify", unit], { stdio: ["ignore", "pipe", "pipe"] });
  } catch (e) { stderr += String(e.stderr || ""); }
  assert.equal(stderr.trim(), "", `systemd-analyze verify must be silent for the shipped unit; got: ${stderr.trim()}`);
});

// installLinux must VERIFY the unit with systemd-analyze before enabling it.
// The fakes below stand in for systemd-analyze/systemctl/loginctl so the test
// never touches the real machine; a fake that writes to stderr with exit 0
// mirrors the real verify, which exits 0 while warning about an unknown key.

function fakeBinDir(t, { verifyStderr = "", verifyFail = false } = {}) {
  const fs = require("fs"), os = require("os"), path = require("path");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-bin-"));
  const log = path.join(dir, "calls.log");
  const script = (name, body) => {
    const p = path.join(dir, name);
    fs.writeFileSync(name.startsWith("/") ? name : path.join(dir, name),
      `#!/bin/sh\necho "$1 $2 $3" >> ${JSON.stringify(log)}\n${body}\n`);
    fs.chmodSync(p, 0o755);
  };
  script("systemd-analyze", verifyFail
    ? `echo ${JSON.stringify(verifyStderr)} >&2; exit 1`
    : `[ -n "${verifyStderr}" ] && echo ${JSON.stringify(verifyStderr)} >&2; exit 0`);
  script("systemctl", "exit 0");
  script("loginctl", "exit 0");
  return { dir, log };
}

function withPath(dir, fn) {
  const saved = process.env.PATH;
  process.env.PATH = `${dir}:${saved}`;
  return Promise.resolve().then(fn).finally(() => { process.env.PATH = saved; });
}

test("installLinux runs systemd-analyze verify on the written unit and enables only when it is silent", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  if (process.platform !== "linux") return;
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-home-"));
  const { dir: bindir, log } = fakeBinDir(null);
  const savedHome = process.env.HOME;
  process.env.HOME = home;
  try {
    await withPath(bindir, async () => {
      const r = await a.installLinux({ exe: "/opt/c/brain", envFile: "/h/.env" });
      assert.equal(r.ok, true, `install should succeed: ${r.error || ""}`);
      const calls = fs.readFileSync(log, "utf8");
      assert.match(calls, /verify .*constellation\.service/, "verify ran on the written unit");
      assert.match(calls, /daemon-reload/, "daemon-reload ran after verify");
      assert.match(calls, /enable --now/, "unit was enabled");
    });
  } finally { process.env.HOME = savedHome; }
});

test("installLinux fails the install when systemd-analyze verify reports a problem (even with exit 0)", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  if (process.platform !== "linux") return;
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-home-"));
  const { dir: bindir, log } = fakeBinDir(null,
    { verifyStderr: "constellation.service:13: Unknown key 'StartLimitIntervalSec' in section [Service], ignoring." });
  const savedHome = process.env.HOME;
  process.env.HOME = home;
  try {
    await withPath(bindir, async () => {
      const r = await a.installLinux({ exe: "/opt/c/brain", envFile: "/h/.env" });
      assert.equal(r.ok, false, "a warned unit must fail the install, not ship broken");
      assert.match(r.error, /verify|Unknown key/i);
      const calls = fs.readFileSync(log, "utf8");
      assert.doesNotMatch(calls, /daemon-reload/, "never reload a unit that failed verification");
      assert.doesNotMatch(calls, /enable/, "never enable a unit that failed verification");
    });
  } finally { process.env.HOME = savedHome; }
});

test("installWindows fails when the scheduled task cannot be created", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-"));
  const { dir: bindir } = fakeBinDir(null);
  // schtasks fake that fails on /Create
  const st = path.join(bindir, "schtasks.exe");
  fs.writeFileSync(st, `#!/bin/sh
case "$1" in
  /Create) echo "ERROR: access denied" >&2; exit 1 ;;
esac
exit 0
`);
  fs.chmodSync(st, 0o755);
  fs.writeFileSync(path.join(bindir, "netsh"), "#!/bin/sh\nexit 0\n");
  fs.chmodSync(path.join(bindir, "netsh"), 0o755);
  const savedPath = process.env.PATH;
  process.env.PATH = `${bindir}:${savedPath}`;
  try {
    const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir });
    assert.equal(r.ok, false);
    assert.match(r.error, /schtasks/i);
  } finally { process.env.PATH = savedPath; }
});

test("installWindows surfaces a failed schtasks /Run instead of claiming success", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-"));
  const { dir: bindir } = fakeBinDir(null);
  const st = path.join(bindir, "schtasks.exe");
  fs.writeFileSync(st, `#!/bin/sh
if [ "$1" = "/Run" ]; then echo "ERROR: The task is not ready to run" >&2; exit 1; fi
exit 0
`);
  fs.chmodSync(st, 0o755);
  fs.writeFileSync(path.join(bindir, "netsh"), "#!/bin/sh\nexit 0\n");
  fs.chmodSync(path.join(bindir, "netsh"), 0o755);
  const savedPath = process.env.PATH;
  process.env.PATH = `${bindir}:${savedPath}`;
  try {
    const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir });
    assert.equal(r.ok, false, "a task that could not be started must not report success");
    assert.match(r.error, /\/Run|start/i);
  } finally { process.env.PATH = savedPath; }
});

test("installWindows surfaces a failed firewall rule instead of claiming success", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-"));
  const { dir: bindir } = fakeBinDir(null);
  const st = path.join(bindir, "schtasks.exe");
  fs.writeFileSync(st, "#!/bin/sh\nexit 0\n");
  fs.chmodSync(st, 0o755);
  const ns = path.join(bindir, "netsh");
  fs.writeFileSync(ns, "#!/bin/sh\necho 'The requested operation requires elevation' >&2; exit 1\n");
  fs.chmodSync(ns, 0o755);
  const savedPath = process.env.PATH;
  process.env.PATH = `${bindir}:${savedPath}`;
  try {
    const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir });
    assert.equal(r.ok, false, "a missing firewall rule must not report success");
    assert.match(r.error, /firewall/i);
  } finally { process.env.PATH = savedPath; }
});

test("installWindows happy path: create, then run, then firewall", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-"));
  const { dir: bindir, log } = fakeBinDir(null);
  const stc = path.join(bindir, "schtasks.exe");
  fs.writeFileSync(stc, `#!/bin/sh\necho "$1 $2 $3" >> ${JSON.stringify(log)}\nexit 0\n`);
  fs.chmodSync(stc, 0o755);
  const nsc = path.join(bindir, "netsh");
  fs.writeFileSync(nsc, `#!/bin/sh\necho "$1 $2 $3" >> ${JSON.stringify(log)}\nexit 0\n`);
  fs.chmodSync(nsc, 0o755);
  const savedPath = process.env.PATH;
  process.env.PATH = `${bindir}:${savedPath}`;
  try {
    const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir });
    assert.equal(r.ok, true, r.error || "");
    const calls = fs.readFileSync(log, "utf8");
    const createAt = calls.indexOf("/Create");
    const runAt = calls.indexOf("/Run");
    const fwAt = calls.indexOf("advfirewall");
    assert.ok(createAt >= 0 && runAt > createAt && fwAt > runAt,
      `expected create -> run -> firewall, got: ${calls}`);
    assert.equal(r.task, a.TASK);
    assert.equal(r.started, true, "success reports that startup was verified");
  } finally { process.env.PATH = savedPath; }
});
