"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const a = require("../autostart");

// Realistic Windows discovery fixtures. schtasks /Query /XML prints the
// registered definition; netsh prints one block per matching rule then "Ok.".
const VALID_TASK_XML = a.windowsTaskXml({ launcher: "C:\\old\\start.cmd", user: "HOME\\old" }).trim();
const NETSH_PRESENT = [
  "",
  "Rule Name:                            Constellation",
  "----------------------------------------------------------------------",
  "Enabled:                              Yes",
  "Direction:                            In",
  "Profiles:                             Private",
  "Grouping:                             ",
  "LocalIP:                              Any",
  "RemoteIP:                             Any",
  "Protocol:                             TCP",
  "LocalPort:                            8484,8485",
  "RemotePort:                           Any",
  "Edge traversal:                       No",
  "Action:                               Allow",
  "Ok.",
  "",
].join("\r\n");
const NETSH_ABSENT = { ok: false, code: 1, out: "No rules match the specified criteria.\r\n", err: "" };
const TASK_ABSENT = { ok: false, code: 1, out: "", err: "ERROR: The system cannot find the file specified.\r\n" };

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
  script("systemctl", `case "$2" in
  is-enabled) echo "not-found"; exit 1 ;;
  is-active) echo "inactive"; exit 3 ;;
esac
exit 0`);
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
      assert.doesNotMatch(calls, /(?:^|\n)--user enable --now(?: |\n)/, "never enable a unit that failed verification");
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
  /Query) echo "ERROR: The system cannot find the file specified." >&2; exit 1 ;;
  /Create) echo "ERROR: access denied" >&2; exit 1 ;;
esac
exit 0
`);
  fs.chmodSync(st, 0o755);
  fs.writeFileSync(path.join(bindir, "netsh"), "#!/bin/sh\necho 'No rules match the specified criteria.' >&2; exit 1\n");
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
if [ "$1" = "/Query" ]; then echo "ERROR: The system cannot find the file specified." >&2; exit 1; fi
if [ "$1" = "/Run" ]; then echo "ERROR: The task is not ready to run" >&2; exit 1; fi
exit 0
`);
  fs.chmodSync(st, 0o755);
  fs.writeFileSync(path.join(bindir, "netsh"), "#!/bin/sh\necho 'No rules match the specified criteria.' >&2; exit 1\n");
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
  fs.writeFileSync(st, "#!/bin/sh\nif [ \"$1\" = \"/Query\" ]; then echo 'ERROR: The system cannot find the file specified.' >&2; exit 1; fi\nexit 0\n");
  fs.chmodSync(st, 0o755);
  const ns = path.join(bindir, "netsh");
  fs.writeFileSync(ns, "#!/bin/sh\nif [ \"$3\" = \"show\" ]; then echo 'No rules match the specified criteria.' >&2; exit 1; fi\necho 'The requested operation requires elevation' >&2; exit 1\n");
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
  fs.writeFileSync(stc, `#!/bin/sh\necho "$1 $2 $3" >> ${JSON.stringify(log)}\nif [ "$1" = "/Query" ]; then echo "ERROR: The system cannot find the file specified." >&2; exit 1; fi\nexit 0\n`);
  fs.chmodSync(stc, 0o755);
  const nsc = path.join(bindir, "netsh");
  fs.writeFileSync(nsc, `#!/bin/sh\necho "$1 $2 $3" >> ${JSON.stringify(log)}\nif [ "$3" = "show" ]; then echo "No rules match the specified criteria." >&2; exit 1; fi\nexit 0\n`);
  fs.chmodSync(nsc, 0o755);
  const savedPath = process.env.PATH;
  process.env.PATH = `${bindir}:${savedPath}`;
  try {
    const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir });
    assert.equal(r.ok, true, r.error || "");
    const calls = fs.readFileSync(log, "utf8");
    const createAt = calls.indexOf("/Create");
    const runAt = calls.indexOf("/Run");
    const fwAt = calls.indexOf("advfirewall firewall add");
    assert.ok(createAt >= 0 && runAt > createAt && fwAt > runAt,
      `expected preflight, then create -> run -> firewall add, got: ${calls}`);
    assert.equal(r.task, a.TASK);
    assert.equal(r.started, true, "success reports that startup was verified");
  } finally { process.env.PATH = savedPath; }
});

// Transactional repair regressions. Injected runners keep these platform
// independent while temporary files exercise real restore/delete behavior.
test("Linux success reports started; failed linger earns login, never boot", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-linux-tx-"));
  const run = async (cmd, args) => {
    if (cmd === "loginctl") return { ok: false, out: "", err: "denied" };
    if (args.includes("is-enabled")) return { ok: false, out: "not-found\n", err: "" };
    if (args.includes("is-active")) return { ok: false, out: "inactive\n", err: "" };
    return { ok: true, out: "", err: "" };
  };
  const r = await a.installLinux({ exe: "/opt/c/brain", envFile: "/h/.env" }, { run, home, user: "family" });
  assert.equal(r.ok, true);
  assert.equal(r.started, true);
  assert.equal(r.startsOnLogin, true);
  assert.equal(r.startsOnBoot, false);
});

test("Linux verification failure restores a prior managed unit and removes a new unit", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  for (const prior of [a.MARKER + "\n[Service]\nExecStart=/old\n", null]) {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-linux-tx-"));
    const dir = path.join(home, ".config", "systemd", "user");
    fs.mkdirSync(dir, { recursive: true });
    const unit = path.join(dir, a.UNIT);
    if (prior != null) fs.writeFileSync(unit, prior);
    const run = async (cmd, args) => {
      if (cmd === "systemd-analyze") return { ok: false, out: "", err: "invalid unit" };
      if (args.includes("is-enabled")) return { ok: false, out: "disabled\n", err: "" };
      if (args.includes("is-active")) return { ok: false, out: "inactive\n", err: "" };
      return { ok: true, out: "", err: "" };
    };
    const r = await a.installLinux({ exe: "/opt/c/brain", envFile: "/h/.env" }, { run, home, user: "family" });
    assert.equal(r.ok, false);
    assert.equal(fs.existsSync(unit), prior != null);
    if (prior != null) assert.equal(fs.readFileSync(unit, "utf8"), prior);
  }
});

test("Linux command throw is a failed install and restores the unit", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-linux-tx-"));
  const run = async () => { throw new Error("verify timed out"); };
  const r = await a.installLinux({ exe: "/opt/c/brain", envFile: "/h/.env" }, { run, home, user: "family" });
  assert.equal(r.ok, false);
  assert.match(r.error, /timed out/);
  assert.equal(fs.existsSync(path.join(home, ".config", "systemd", "user", a.UNIT)), false);
});

test("Linux readiness rollback restores prior unit and its enabled/running state", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-linux-tx-"));
  const dir = path.join(home, ".config", "systemd", "user");
  fs.mkdirSync(dir, { recursive: true });
  const unit = path.join(dir, a.UNIT);
  const prior = a.MARKER + "\n[Service]\nExecStart=/old\n";
  fs.writeFileSync(unit, prior);
  const calls = [];
  const run = async (cmd, args) => {
    calls.push([cmd, ...args]);
    if (args.includes("is-enabled")) return { ok: true, out: "enabled\n", err: "" };
    if (args.includes("is-active")) return { ok: true, out: "active\n", err: "" };
    return { ok: true, out: "", err: "" };
  };
  const r = await a.installLinux({ exe: "/opt/c/brain", envFile: "/h/.env" }, { run, home, user: "family" });
  assert.equal(r.ok, true);
  assert.equal((await r.rollback()).ok, true);
  assert.equal(fs.readFileSync(unit, "utf8"), prior);
  assert.ok(calls.some((c) => c.includes("enable") && !c.includes("--now")), "prior enabled state restored");
  assert.ok(calls.some((c) => c.includes("start")), "prior active state restored");
});

test("Linux enable --now failure restores exact prior state and aggregates every rollback failure", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-linux-tx-"));
  const dir = path.join(home, ".config", "systemd", "user");
  fs.mkdirSync(dir, { recursive: true });
  const unit = path.join(dir, a.UNIT);
  const prior = Buffer.concat([Buffer.from(a.MARKER + "\n[Service]\nExecStart=/old\n"), Buffer.from([0xff, 0x00, 0xfe])]);
  fs.writeFileSync(unit, prior);
  const calls = [];
  let reloads = 0;
  const run = async (cmd, args) => {
    calls.push([cmd, ...args]);
    if (args.includes("is-enabled")) return { ok: false, out: "disabled\n", err: "" };
    if (args.includes("is-active")) return { ok: false, out: "inactive\n", err: "" };
    if (args.includes("enable") && args.includes("--now")) return { ok: false, out: "", err: "enable exploded" };
    if (args.includes("disable") && args.includes("--now")) return { ok: false, out: "", err: "disable cleanup failed" };
    if (args.includes("daemon-reload") && ++reloads > 1) return { ok: false, out: "", err: "reload cleanup failed" };
    if (args.includes("disable")) return { ok: false, out: "", err: "disable restore failed" };
    if (args.includes("stop")) return { ok: false, out: "", err: "stop restore failed" };
    return { ok: true, out: "", err: "" };
  };
  const result = await a.installLinux({ exe: "/opt/c/brain", envFile: "/h/.env" }, { run, home, user: "family" });
  assert.equal(result.ok, false);
  assert.deepEqual(fs.readFileSync(unit), prior);
  assert.match(result.error, /enable exploded/);
  assert.match(result.error, /disable cleanup failed/);
  assert.match(result.error, /reload cleanup failed/);
  assert.match(result.error, /disable restore failed/);
  assert.match(result.error, /stop restore failed/);
  assert.ok(calls.some((c) => c.includes("stop")), "inactive state is restored explicitly after a partial start");
});

test("Linux readiness rollback attempts enabled and active restoration after earlier cleanup failures", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-linux-tx-"));
  const dir = path.join(home, ".config", "systemd", "user");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, a.UNIT), a.MARKER + "\n[Service]\nExecStart=/old\n");
  const calls = [];
  const run = async (cmd, args) => {
    calls.push([cmd, ...args]);
    if (args.includes("is-enabled")) return { ok: true, out: "enabled\n", err: "" };
    if (args.includes("is-active")) return { ok: true, out: "active\n", err: "" };
    if (args.includes("disable") && args.includes("--now")) return { ok: false, out: "", err: "disable failed" };
    if (args.includes("daemon-reload") && calls.filter((c) => c.includes("daemon-reload")).length > 1)
      return { ok: false, out: "", err: "reload failed" };
    if (args.includes("enable") && !args.includes("--now")) return { ok: false, out: "", err: "enable restore failed" };
    if (args.includes("start")) return { ok: false, out: "", err: "start restore failed" };
    return { ok: true, out: "", err: "" };
  };
  const installed = await a.installLinux({ exe: "/opt/c/brain", envFile: "/h/.env" }, { run, home, user: "family" });
  assert.equal(installed.ok, true);
  const rolled = await installed.rollback();
  assert.equal(rolled.ok, false);
  for (const message of ["disable failed", "reload failed", "enable restore failed", "start restore failed"])
    assert.match(rolled.error, new RegExp(message));
  assert.ok(calls.some((c) => c.includes("enable") && !c.includes("--now")));
  assert.ok(calls.some((c) => c.includes("start")));
});

test("Linux aborts before creating a unit when discovery transport fails", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-linux-discovery-"));
  const calls = [];
  const run = async (cmd, args) => {
    calls.push([cmd, ...args]);
    return { ok: false, code: 1, out: "", err: "Failed to connect to bus: No medium found" };
  };
  const result = await a.installLinux({ exe: "/opt/c/brain", envFile: "/h/.env" }, { run, home, user: "family" });
  assert.equal(result.ok, false);
  assert.match(result.error, /is-enabled.*No medium found/i);
  assert.equal(fs.existsSync(path.join(home, ".config", "systemd", "user", a.UNIT)), false);
  assert.deepEqual(calls, [["systemctl", "--user", "is-enabled", a.UNIT]]);
});

test("Linux aborts before writing when prior systemd state is unknown", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  for (const failedQuery of ["is-enabled", "is-active"]) {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-linux-discovery-"));
    const dir = path.join(home, ".config", "systemd", "user");
    fs.mkdirSync(dir, { recursive: true });
    const unit = path.join(dir, a.UNIT);
    const prior = a.MARKER + "\n[Service]\nExecStart=/old\n";
    fs.writeFileSync(unit, prior);
    const calls = [];
    const run = async (cmd, args) => {
      calls.push([cmd, ...args]);
      if (args.includes(failedQuery)) return { ok: false, code: 1, out: "", err: "Failed to connect to bus: Permission denied" };
      if (args.includes("is-enabled")) return { ok: false, code: 1, out: "disabled\n", err: "" };
      if (args.includes("is-active")) return { ok: false, code: 3, out: "inactive\n", err: "" };
      return { ok: true, code: 0, out: "", err: "" };
    };
    const result = await a.installLinux({ exe: "/opt/c/brain", envFile: "/h/.env" }, { run, home, user: "family" });
    assert.equal(result.ok, false);
    assert.match(result.error, new RegExp(`${failedQuery}.*Permission denied`, "i"));
    assert.equal(fs.readFileSync(unit, "utf8"), prior, "discovery failure leaves the unit byte-for-byte untouched");
    assert.equal(calls.some((call) => call[0] === "systemd-analyze" ||
      call.includes("daemon-reload") || call.includes("enable") || call.includes("disable") ||
      call.includes("start") || call.includes("stop")), false, "unknown state permits no mutation");
  }
});

test("Linux rollback of a fresh install restores absence without stopping a nonexistent unit", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-linux-discovery-"));
  const calls = [];
  const run = async (cmd, args) => {
    calls.push([cmd, ...args]);
    if (args.includes("is-enabled")) return { ok: false, code: 4, out: "not-found\n", err: "" };
    if (args.includes("is-active")) return { ok: false, code: 4, out: "inactive\n", err: "" };
    if (args.includes("stop")) return { ok: false, code: 5, out: "", err: "Unit constellation.service not loaded." };
    return { ok: true, code: 0, out: "", err: "" };
  };
  const installed = await a.installLinux({ exe: "/opt/c/brain", envFile: "/h/.env" }, { run, home, user: "family" });
  assert.equal(installed.ok, true, installed.error || "");
  assert.equal((await installed.rollback()).ok, true);
  assert.equal(fs.existsSync(path.join(home, ".config", "systemd", "user", a.UNIT)), false);
  assert.equal(calls.some((call) => call.includes("stop")), false);
});

test("Linux recognizes explicit disabled and inactive states and restores them exactly", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cst-linux-discovery-"));
  const dir = path.join(home, ".config", "systemd", "user");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, a.UNIT), a.MARKER + "\n[Service]\nExecStart=/old\n");
  const calls = [];
  const run = async (cmd, args) => {
    calls.push([cmd, ...args]);
    if (args.includes("is-enabled")) return { ok: false, code: 1, out: "disabled\n", err: "" };
    if (args.includes("is-active")) return { ok: false, code: 3, out: "inactive\n", err: "" };
    return { ok: true, code: 0, out: "", err: "" };
  };
  const installed = await a.installLinux({ exe: "/opt/c/brain", envFile: "/h/.env" }, { run, home, user: "family" });
  assert.equal(installed.ok, true, installed.error || "");
  assert.equal((await installed.rollback()).ok, true);
  assert.ok(calls.some((call) => call.includes("disable") && !call.includes("--now")));
  assert.ok(calls.some((call) => call.includes("stop")));
});

function windowsRun({ taskXml = null, firewallExists = false, cleanupFails = [] } = {}) {
  const calls = [];
  const run = async (cmd, args) => {
    calls.push([cmd, ...args]);
    const joined = [cmd, ...args].join(" ");
    if (cmd === "schtasks.exe" && args[0] === "/Query")
      return taskXml == null
        ? { ok: false, code: 1, out: "", err: "ERROR: The system cannot find the file specified." }
        : { ok: true, code: 0, out: taskXml, err: "" };
    if (cmd === "netsh" && args.includes("show"))
      return firewallExists
        ? { ok: true, code: 0, out: NETSH_PRESENT, err: "" }
        : { ok: false, code: 1, out: "", err: "No rules match the specified criteria." };
    if (cleanupFails.some((x) => joined.includes(x))) return { ok: false, out: "", err: `${joined} cleanup failed` };
    return { ok: true, out: "", err: "" };
  };
  return { run, calls };
}

test("Windows aborts before writes when task or firewall discovery is unknown", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const unknowns = [
    { label: "task access denied", task: { ok: false, code: 5, out: "", err: "ERROR: Access is denied." },
      firewall: { ok: false, code: 1, out: "", err: "No rules match the specified criteria." } },
    { label: "localized task absence", task: { ok: false, code: 1, out: "", err: "ERREUR : Le fichier spécifié est introuvable." },
      firewall: { ok: false, code: 1, out: "", err: "No rules match the specified criteria." } },
    { label: "malformed task XML", task: { ok: true, code: 0, out: "task exists but XML is unavailable", err: "" },
      firewall: { ok: false, code: 1, out: "", err: "No rules match the specified criteria." } },
    { label: "firewall access denied", task: { ok: false, code: 1, out: "", err: "ERROR: The system cannot find the file specified." },
      firewall: { ok: false, code: 5, out: "", err: "Access is denied." } },
    { label: "localized firewall absence", task: { ok: false, code: 1, out: "", err: "ERROR: The system cannot find the file specified." },
      firewall: { ok: false, code: 1, out: "", err: "Aucune règle ne correspond aux critères spécifiés." } },
    { label: "ambiguous firewall output", task: { ok: false, code: 1, out: "", err: "ERROR: The system cannot find the file specified." },
      firewall: { ok: true, code: 0, out: "Ok.", err: "" } },
  ];
  for (const item of unknowns) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-discovery-"));
    const launcher = path.join(dataDir, "start-constellation.cmd");
    const xml = path.join(dataDir, "constellation-task.xml");
    fs.writeFileSync(launcher, "old launcher");
    fs.writeFileSync(xml, "old xml");
    const calls = [];
    const run = async (cmd, args) => {
      calls.push([cmd, ...args]);
      if (cmd === "schtasks.exe") return item.task;
      if (cmd === "netsh" && args.includes("show")) return item.firewall;
      return { ok: true, code: 0, out: "", err: "" };
    };
    const result = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir },
      { run, user: "HOME\\x" });
    assert.equal(result.ok, false, item.label);
    assert.match(result.error, /snapshot|discover|query|parse|prior/i, item.label);
    assert.equal(fs.readFileSync(launcher, "utf8"), "old launcher", item.label);
    assert.equal(fs.readFileSync(xml, "utf8"), "old xml", item.label);
    assert.equal(calls.some((call) => call.includes("/Create") || call.includes("/Delete") ||
      call.includes("add") || call.includes("delete")), false, `${item.label}: no mutation`);
  }
});

// Review blocker: task XML must be parsed, not pattern-matched. Anything a
// real XML parser rejects, or that is not a Task Scheduler <Task>, is an
// unknown prior state; install must stop before writing a single byte.
test("Windows rejects malformed or foreign task XML before any write or mutation", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const ns = 'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"';
  const invalid = {
    "mismatched tag (review repro)": '<?xml version="1.0"?><Task><Broken></Task>',
    "mismatched tag in namespace": `<?xml version="1.0" encoding="UTF-16"?><Task version="1.4" ${ns}><Broken></Task>`,
    "unclosed child": `<Task ${ns}><Settings>`,
    "trailing garbage after root": `<Task ${ns}><Settings/></Task>trailing`,
    "second root element": `<Task ${ns}/><Task ${ns}/>`,
    "undefined entity": `<Task ${ns}><Settings>&bogus;</Settings></Task>`,
    "doctype with entity": `<!DOCTYPE Task [<!ENTITY x "y">]><Task ${ns}>&x;</Task>`,
    "doctype without entity use": `<!DOCTYPE Task SYSTEM "http://example.invalid/t.dtd"><Task ${ns}><Settings/></Task>`,
    "duplicate attribute": `<Task version="1.2" version="1.3" ${ns}/>`,
    "no namespace": "<Task><Settings/></Task>",
    "wrong namespace": '<Task xmlns="urn:not-task-scheduler"><Settings/></Task>',
    "wrong root": `<Tasks ${ns}><Settings/></Tasks>`,
    "prefixed wrong local name": '<t:Job xmlns:t="http://schemas.microsoft.com/windows/2004/02/mit/task"/>',
    "empty output": "",
  };
  for (const [label, xml] of Object.entries(invalid)) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-xml-"));
    const calls = [];
    const run = async (cmd, args) => {
      calls.push([cmd, ...args]);
      if (cmd === "schtasks.exe" && args[0] === "/Query") return { ok: true, code: 0, out: xml, err: "" };
      if (cmd === "netsh" && args.includes("show")) return NETSH_ABSENT;
      return { ok: true, code: 0, out: "", err: "" };
    };
    const result = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir },
      { run, user: "HOME\\x" });
    assert.equal(result.ok, false, label);
    assert.match(result.error, /scheduled task snapshot/i, label);
    assert.deepEqual(fs.readdirSync(dataDir), [], `${label}: no file writes`);
    assert.equal(calls.some((call) => call.includes("/Create") || call.includes("/Delete") ||
      call.includes("/Run") || call.includes("add") || call.includes("delete")), false, `${label}: no mutation`);
  }
});

test("Windows task snapshot parser accepts real Task Scheduler XML, prefixed or default namespace", () => {
  const snap = a.scheduledTaskSnapshot({ ok: true, code: 0, out: "\ufeff" + VALID_TASK_XML + "\r\n", err: "" });
  assert.equal(snap.known, true, snap.error);
  assert.equal(snap.xml, VALID_TASK_XML);
  const prefixed = a.scheduledTaskSnapshot({ ok: true, code: 0, err: "",
    out: '<t:Task xmlns:t="http://schemas.microsoft.com/windows/2004/02/mit/task"><t:Settings/></t:Task>' });
  assert.equal(prefixed.known, true, prefixed.error);
  const absent = a.scheduledTaskSnapshot(TASK_ABSENT);
  assert.deepEqual(absent, { known: true, xml: null });
  for (const r of [
    { ok: false, code: 1, out: VALID_TASK_XML, err: "" },
    { ok: true, code: 0, out: VALID_TASK_XML, err: "WARNING: something" },
  ]) assert.equal(a.scheduledTaskSnapshot(r).known, false, JSON.stringify(r).slice(0, 60));
});

test("Windows preexisting task that parsed as valid is restored byte-for-byte on rollback", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-restore-"));
  let restored = null;
  const fake = windowsRun({ taskXml: VALID_TASK_XML, firewallExists: true });
  let creates = 0;
  const run = async (cmd, args) => {
    if (cmd === "schtasks.exe" && args[0] === "/Create" && ++creates === 2) restored = fs.readFileSync(args[4]);
    if (cmd === "schtasks.exe" && args[0] === "/Run") return { ok: false, out: "", err: "run failed" };
    return fake.run(cmd, args);
  };
  const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir }, { run, user: "HOME\\x" });
  assert.equal(r.ok, false);
  assert.ok(restored, "rollback re-created the prior task");
  assert.equal(restored.subarray(2).toString("utf16le"), VALID_TASK_XML);
});

// Review blocker: firewall presence must come from an exact, unambiguous
// English netsh shape. Any extra/unknown line, access error, nonzero exit, or
// conflicting signal is unknown and must stop the install before mutation.
test("Windows rejects ambiguous firewall discovery output before any write or mutation", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const lines = NETSH_PRESENT.split("\r\n");
  const bad = {
    "rule name then access denied (review repro)": { ok: true, code: 0, out: "Rule Name: Constellation\r\nAccess is denied.\r\n", err: "" },
    "full block plus access denied": { ok: true, code: 0, out: NETSH_PRESENT.replace("Ok.", "Access is denied.\r\nOk."), err: "" },
    "full block with stderr": { ok: true, code: 0, out: NETSH_PRESENT, err: "Access is denied." },
    "full block nonzero exit": { ok: false, code: 1, out: NETSH_PRESENT, err: "" },
    "block missing Ok.": { ok: true, code: 0, out: lines.filter((l) => l !== "Ok.").join("\r\n"), err: "" },
    "block missing separator": { ok: true, code: 0, out: lines.filter((l) => !l.startsWith("---")).join("\r\n"), err: "" },
    "bare rule name line": { ok: true, code: 0, out: "Rule Name: Constellation\r\nOk.\r\n", err: "" },
    "other rule name": { ok: true, code: 0, out: NETSH_PRESENT.replace("Constellation", "Constellation2"), err: "" },
    "present and absent together": { ok: true, code: 0, out: NETSH_PRESENT + "No rules match the specified criteria.\r\n", err: "" },
    "absent with extra text": { ok: false, code: 1, out: "No rules match the specified criteria.\r\nAccess is denied.\r\n", err: "" },
    "absent in both streams": { ok: false, code: 1, out: "No rules match the specified criteria.", err: "No rules match the specified criteria." },
    "absent with odd exit code": { ok: false, code: 5, out: "No rules match the specified criteria.", err: "" },
    "localized present": { ok: true, code: 0, out: NETSH_PRESENT.replace("Rule Name:", "Nom de la règle :"), err: "" },
    "unknown field": { ok: true, code: 0, out: NETSH_PRESENT.replace("Enabled:", "Mystery:"), err: "" },
    "bad enabled value": { ok: true, code: 0, out: NETSH_PRESENT.replace(/Enabled:( +)Yes/, "Enabled:$1Oui"), err: "" },
    "only Ok.": { ok: true, code: 0, out: "Ok.\r\n", err: "" },
    "empty success": { ok: true, code: 0, out: "", err: "" },
  };
  for (const [label, firewall] of Object.entries(bad)) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-fw-"));
    const calls = [];
    const run = async (cmd, args) => {
      calls.push([cmd, ...args]);
      if (cmd === "schtasks.exe" && args[0] === "/Query") return TASK_ABSENT;
      if (cmd === "netsh" && args.includes("show")) return firewall;
      return { ok: true, code: 0, out: "", err: "" };
    };
    const result = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir },
      { run, user: "HOME\\x" });
    assert.equal(result.ok, false, label);
    assert.match(result.error, /firewall snapshot/i, label);
    assert.deepEqual(fs.readdirSync(dataDir), [], `${label}: no file writes`);
    assert.equal(calls.some((call) => call.includes("/Create") || call.includes("/Delete") ||
      call.includes("/Run") || call.includes("add") || call.includes("delete")), false, `${label}: no mutation`);
    assert.equal(a.firewallSnapshot(firewall).known, false, `${label}: parser must report unknown`);
  }
});

test("Windows firewall parser recognizes only exact present and absent shapes", () => {
  assert.deepEqual(a.firewallSnapshot({ ok: true, code: 0, out: NETSH_PRESENT, err: "" }), { known: true, existed: true });
  const twoBlocks = NETSH_PRESENT.replace("Ok.\r\n", "") + NETSH_PRESENT;
  assert.deepEqual(a.firewallSnapshot({ ok: true, code: 0, out: twoBlocks, err: "" }), { known: true, existed: true });
  assert.deepEqual(a.firewallSnapshot(NETSH_ABSENT), { known: true, existed: false });
  assert.deepEqual(a.firewallSnapshot({ ok: false, code: 1, out: "", err: "No rules match the specified criteria.\r\n" }),
    { known: true, existed: false });
});

// Review test gap: each rule below was implemented but no test failed when
// it was removed. These pin them individually.
test("task XML that the parser only WARNS about is still an unknown prior state", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const ns = 'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"';
  // xmldom recovers from these with a warning (no error) and still yields a <Task> root
  const warnedOnly = {
    "unquoted attribute value on root": `<Task version=1.4 ${ns}><Settings/></Task>`,
    "unquoted attribute value on child": `<Task ${ns}><Settings Context=Author/></Task>`,
  };
  for (const [label, xml] of Object.entries(warnedOnly)) {
    const parsed = a.parseTaskXml(xml);
    assert.equal(parsed.ok, false, `${label}: parser warnings must reject`);
    assert.match(parsed.error, /warning/i, label);
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-xmlwarn-"));
    const calls = [];
    const run = async (cmd, args) => {
      calls.push([cmd, ...args]);
      if (cmd === "schtasks.exe" && args[0] === "/Query") return { ok: true, code: 0, out: xml, err: "" };
      if (cmd === "netsh" && args.includes("show")) return NETSH_ABSENT;
      return { ok: true, code: 0, out: "", err: "" };
    };
    const result = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir }, { run, user: "HOME\\x" });
    assert.equal(result.ok, false, label);
    assert.match(result.error, /scheduled task snapshot/i, label);
    assert.deepEqual(fs.readdirSync(dataDir), [], `${label}: no file writes`);
    assert.equal(calls.some((c) => c.includes("/Create") || c.includes("/Run") || c.includes("add")), false, `${label}: no mutation`);
  }
});

test("firewall present block must contain every known field exactly once", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const lines = NETSH_PRESENT.split("\r\n");
  const without = (prefix) => lines.filter((l) => !l.startsWith(prefix)).join("\r\n");
  const withExtra = (after, extra) => lines.flatMap((l) => (l.startsWith(after) ? [l, extra] : [l])).join("\r\n");
  const bad = {
    "missing Action": without("Action:"),
    "missing Grouping": without("Grouping:"),
    "missing Edge traversal": without("Edge traversal:"),
    "duplicate Enabled (conflicting)": withExtra("Enabled:", "Enabled:                              No"),
    "duplicate Action (identical)": withExtra("Action:", "Action:                               Allow"),
    "duplicate field replacing a missing one": without("Grouping:").replace(
      /(Protocol:[^\r\n]*)/, "$1\r\nProtocol:                             UDP"),
  };
  for (const [label, out] of Object.entries(bad)) {
    const firewall = { ok: true, code: 0, out, err: "" };
    assert.equal(a.firewallSnapshot(firewall).known, false, `${label}: parser must report unknown`);
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-fwfields-"));
    const calls = [];
    const run = async (cmd, args) => {
      calls.push([cmd, ...args]);
      if (cmd === "schtasks.exe" && args[0] === "/Query") return TASK_ABSENT;
      if (cmd === "netsh" && args.includes("show")) return firewall;
      return { ok: true, code: 0, out: "", err: "" };
    };
    const result = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir }, { run, user: "HOME\\x" });
    assert.equal(result.ok, false, label);
    assert.match(result.error, /firewall snapshot/i, label);
    assert.deepEqual(fs.readdirSync(dataDir), [], `${label}: no file writes`);
    assert.equal(calls.some((c) => c.includes("/Create") || c.includes("/Run") || c.includes("add")), false, `${label}: no mutation`);
  }
  // control: the unmodified block is still known-present
  assert.deepEqual(a.firewallSnapshot({ ok: true, code: 0, out: NETSH_PRESENT, err: "" }), { known: true, existed: true });
});

// Review gap: a Windows user name/path with non-English characters comes back
// from `schtasks /Query /XML` in the console code page. Reading it as UTF-8
// turned "é" into U+FFFD and aborted every reinstall over an existing task.
// Output is now captured as raw bytes and decoded by BOM, else by the active
// console code page; anything undecodable (or containing U+FFFD) is unknown.
const INTL_USER = "HOME\\José";
const INTL_XML = [
  '<?xml version="1.0" encoding="UTF-16"?>',
  '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">',
  `  <Principals><Principal id="Author"><UserId>${INTL_USER}</UserId></Principal></Principals>`,
  "  <Actions Context=\"Author\"><Exec><Command>C:\\Users\\José\\Constellation\\start-constellation.cmd</Command></Exec></Actions>",
  "</Task>",
].join("\r\n");
// single-byte fixtures: the XML is ASCII except "é"
const singleByte = (text, eAcute) => Buffer.concat(text.split("é")
  .flatMap((s, i) => (i ? [Buffer.from([eAcute]), Buffer.from(s, "ascii")] : [Buffer.from(s, "ascii")])));
const FIXTURES = {
  utf16bom: Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from(INTL_XML + "\r\n", "utf16le")]),
  utf16nobom: Buffer.from(INTL_XML + "\r\n", "utf16le"),
  utf8bom: Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from(INTL_XML, "utf8")]),
  utf8: Buffer.from(INTL_XML + "\r\n", "utf8"),
  cp1252: singleByte(INTL_XML + "\r\n", 0xe9),
  cp850: singleByte(INTL_XML + "\r\n", 0x82),
};
const chcpResult = (cp) => ({ ok: true, code: 0, out: `Active code page: ${cp}\r\n`, err: "" });

test("console decoding: BOM wins and needs no code-page lookup", async () => {
  for (const key of ["utf16bom", "utf16nobom", "utf8bom"]) {
    let lookups = 0;
    const d = await a.decodeConsoleOutput(FIXTURES[key], async () => { lookups++; return { ok: true, codePage: 437 }; });
    assert.equal(d.ok, true, `${key}: ${d.error}`);
    assert.equal(d.text.replace(/^\ufeff/, "").trim(), INTL_XML, key);
    assert.equal(lookups, 0, `${key}: encoding is self-describing`);
  }
});

test("console decoding: no BOM decodes with the active console code page", async () => {
  for (const [key, cp] of [["utf8", 65001], ["cp1252", 1252], ["cp850", 850], ["cp850", 437]]) {
    const d = await a.decodeConsoleOutput(FIXTURES[key], async () => ({ ok: true, codePage: cp }));
    assert.equal(d.ok, true, `${key}@${cp}: ${d.error}`);
    assert.equal(d.text.trim(), INTL_XML, `${key}@${cp}`);
    assert.match(d.text, /<UserId>HOME\\José<\/UserId>/);
  }
  // pure ASCII needs no lookup at all
  let lookups = 0;
  const ascii = await a.decodeConsoleOutput(Buffer.from(VALID_TASK_XML.replace("—", "-")), async () => { lookups++; return { ok: false }; });
  assert.equal(ascii.ok, true, ascii.error);
  assert.equal(lookups, 0);
});

test("console decoding fails closed: bad bytes, unknown or unreadable code page, U+FFFD", async () => {
  const cases = {
    "cp1252 bytes read as UTF-8": [FIXTURES.cp1252, { ok: true, codePage: 65001 }],
    "truncated UTF-16LE with BOM": [FIXTURES.utf16bom.subarray(0, FIXTURES.utf16bom.length - 1), { ok: true, codePage: 437 }],
    "byte undefined in windows-1253": [Buffer.from([0x3c, 0x61, 0xd2, 0x3e]), { ok: true, codePage: 1253 }],
    "unsupported code page": [FIXTURES.cp1252, { ok: true, codePage: 12345 }],
    "code page lookup failed": [FIXTURES.cp1252, { ok: false, error: "chcp failed" }],
    "code page lookup failed, bytes happen to be valid UTF-8": [FIXTURES.utf8, { ok: false, error: "chcp failed" }],
    "code page lookup threw": [FIXTURES.utf8, null],
    "literal U+FFFD in UTF-8": [Buffer.from("<a>\ufffd</a>", "utf8"), { ok: true, codePage: 65001 }],
    "NUL byte inside single-byte text": [Buffer.from([0x3c, 0x61, 0x00, 0x3e, 0xe9]), { ok: true, codePage: 437 }],
  };
  for (const [label, [buf, cp]] of Object.entries(cases)) {
    const d = await a.decodeConsoleOutput(buf, async () => { if (cp == null) throw new Error("spawn failed"); return cp; });
    assert.equal(d.ok, false, label);
    assert.ok(d.error, label);
  }
});

test("chcp output parsing reads the one number in English or localized text, else unknown", () => {
  assert.deepEqual(a.consoleCodePage(chcpResult(850)), { ok: true, codePage: 850 });
  assert.deepEqual(a.consoleCodePage({ ok: true, code: 0, out: "Page de codes active : 1252\r\n", err: "" }), { ok: true, codePage: 1252 });
  assert.deepEqual(a.consoleCodePage({ ok: true, code: 0, out: "Aktive Codepage: 65001.\r\n", err: "" }), { ok: true, codePage: 65001 });
  for (const r of [
    { ok: false, code: 1, out: "Active code page: 850", err: "" },
    { ok: true, code: 0, out: "Active code page: 850", err: "noise" },
    { ok: true, code: 0, out: "", err: "" },
    { ok: true, code: 0, out: "Active code page: 850 of 2", err: "" },
  ]) assert.equal(a.consoleCodePage(r).ok, false, JSON.stringify(r));
});

test("task snapshot never accepts text containing U+FFFD or a lossy '?' in an identity field", () => {
  const fffd = INTL_XML.replace("é", "\ufffd");
  const snap = a.scheduledTaskSnapshot({ ok: true, code: 0, out: fffd, err: "" });
  assert.equal(snap.known, false);
  assert.match(snap.error, /U\+FFFD/, "rejected by the explicit decode check, not only by a parser side effect");
  assert.equal(a.scheduledTaskSnapshot({ ok: true, code: 0, out: INTL_XML.replace(/é/g, "?"), err: "" }).known, false,
    "a code page that cannot represent the name makes schtasks print '?'; Windows names never contain '?'");
  assert.equal(a.scheduledTaskSnapshot({ ok: true, code: 0, out: INTL_XML, err: "" }).known, true);
});

test("Windows reinstall over an existing task with a non-English user name restores it exactly", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  for (const [key, cp] of [["utf16bom", null], ["utf8", 65001], ["cp1252", 1252], ["cp850", 850]]) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-intl-"));
    let restored = null, creates = 0;
    const calls = [];
    const run = async (cmd, args) => {
      calls.push([cmd, ...args]);
      if (cmd === "schtasks.exe" && args[0] === "/Query") return { ok: true, code: 0, out: FIXTURES[key], err: "" };
      if (cmd === "cmd.exe" && args.includes("chcp")) return cp == null ? { ok: false, code: 1, out: "", err: "no" } : chcpResult(cp);
      if (cmd === "netsh" && args.includes("show")) return NETSH_ABSENT;
      if (cmd === "schtasks.exe" && args[0] === "/Create" && ++creates === 2) restored = fs.readFileSync(args[4]);
      if (cmd === "schtasks.exe" && args[0] === "/Run") return { ok: false, code: 1, out: "", err: "run failed" };
      return { ok: true, code: 0, out: "", err: "" };
    };
    const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir }, { run, user: INTL_USER });
    assert.equal(r.ok, false, key);
    assert.match(r.error, /\/Run/, `${key}: got past discovery (${r.error})`);
    assert.ok(restored, `${key}: prior task re-created on rollback`);
    assert.equal(restored.subarray(2).toString("utf16le"), INTL_XML, `${key}: restored byte-exact with the real name`);
  }
});

test("Windows aborts before any write when the task query cannot be decoded", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  for (const [bytes, chcp] of [[FIXTURES.cp1252, chcpResult(65001)], [FIXTURES.cp1252, chcpResult(12345)],
    [FIXTURES.cp1252, { ok: false, code: 1, out: "", err: "denied" }],
    [FIXTURES.utf8, { ok: false, code: 1, out: "", err: "denied" }]]) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-intl-"));
    const calls = [];
    const run = async (cmd, args) => {
      calls.push([cmd, ...args]);
      if (cmd === "schtasks.exe" && args[0] === "/Query") return { ok: true, code: 0, out: bytes, err: "" };
      if (cmd === "cmd.exe" && args.includes("chcp")) return chcp;
      if (cmd === "netsh" && args.includes("show")) return NETSH_ABSENT;
      return { ok: true, code: 0, out: "", err: "" };
    };
    const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir }, { run, user: INTL_USER });
    assert.equal(r.ok, false);
    assert.match(r.error, /scheduled task snapshot/i);
    assert.deepEqual(fs.readdirSync(dataDir), []);
    assert.equal(calls.some((c) => c.includes("/Create") || c.includes("/Run") || c.includes("add")), false);
  }
});

test("default runner captures schtasks output as raw bytes (real child process)", async () => {
  if (process.platform === "win32") return;
  const fs = require("fs"), os = require("os"), path = require("path");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-raw-"));
  const bindir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-bin-raw-"));
  const fixture = path.join(bindir, "task.bin");
  fs.writeFileSync(fixture, FIXTURES.cp1252);
  const restoredCopy = path.join(bindir, "restored.xml");
  const write = (name, body) => { fs.writeFileSync(path.join(bindir, name), `#!/bin/sh\n${body}\n`); fs.chmodSync(path.join(bindir, name), 0o755); };
  write("schtasks.exe", `case "$1" in
  /Query) cat ${JSON.stringify(fixture)}; exit 0 ;;
  /Create) cp "$5" ${JSON.stringify(restoredCopy)}; exit 0 ;;
esac
exit 0`);
  write("cmd.exe", 'echo "Active code page: 1252"');
  write("netsh", `if [ "$3" = "show" ]; then echo "No rules match the specified criteria."; exit 1; fi\nexit 0`);
  const savedPath = process.env.PATH;
  process.env.PATH = `${bindir}:${savedPath}`;
  try {
    const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir }, { user: INTL_USER });
    assert.equal(r.ok, true, r.error || "");
    assert.equal((await r.rollback()).ok, true);
    assert.equal(fs.readFileSync(restoredCopy).subarray(2).toString("utf16le"), INTL_XML);
  } finally { process.env.PATH = savedPath; }
});

test("the XML parser ships with the app: @xmldom/xmldom is a runtime dependency", () => {
  const fs = require("fs"), path = require("path");
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));
  assert.ok(pkg.dependencies && pkg.dependencies["@xmldom/xmldom"], "must be in dependencies, not only a dev transitive");
  const lock = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package-lock.json"), "utf8"));
  assert.ok(lock.packages[""].dependencies && lock.packages[""].dependencies["@xmldom/xmldom"]);
  assert.notEqual(lock.packages["node_modules/@xmldom/xmldom"].dev, true, "lockfile must not mark it dev-only");
});

test("Windows accepts only exact machine task XML and exact English absence outcomes", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const missingTask = { ok: false, code: 1, out: "", err: "ERROR: The system cannot find the file specified.\r\n" };
  const missingFirewall = { ok: false, code: 1, out: "", err: "No rules match the specified criteria.\r\n" };
  for (const snapshot of [
    { task: missingTask, firewall: missingFirewall },
    { task: { ok: true, code: 0, out: VALID_TASK_XML + "\r\n", err: "" },
      firewall: missingFirewall },
    { task: missingTask, firewall: { ok: true, code: 0, out: NETSH_PRESENT, err: "" } },
    { task: missingTask,
      firewall: { ok: true, code: 0, out: "No rules match the specified criteria.\r\n", err: "" } },
  ]) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-discovery-"));
    const run = async (cmd, args) => {
      if (cmd === "schtasks.exe" && args[0] === "/Query") return snapshot.task;
      if (cmd === "netsh" && args.includes("show")) return snapshot.firewall;
      return { ok: true, code: 0, out: "", err: "" };
    };
    const result = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir },
      { run, user: "HOME\\x" });
    assert.equal(result.ok, true, result.error || "");
  }
});

test("Windows /Run failure restores preexisting task, launcher, and XML", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-tx-"));
  const launcher = path.join(dataDir, "start-constellation.cmd");
  const xml = path.join(dataDir, "constellation-task.xml");
  fs.writeFileSync(launcher, "old launcher");
  fs.writeFileSync(xml, "old xml");
  const fake = windowsRun({ taskXml: VALID_TASK_XML, firewallExists: true });
  const run = async (cmd, args) => cmd === "schtasks.exe" && args[0] === "/Run"
    ? { ok: false, out: "", err: "run failed" } : fake.run(cmd, args);
  const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir }, { run, user: "HOME\\x" });
  assert.equal(r.ok, false);
  assert.equal(fs.readFileSync(launcher, "utf8"), "old launcher");
  assert.equal(fs.readFileSync(xml, "utf8"), "old xml");
  assert.equal(fake.calls.filter((c) => c[0] === "schtasks.exe" && c[1] === "/Create").length, 2,
    "second create restores the prior task");
});

test("Windows command throw is rolled back instead of escaping", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-tx-"));
  const fake = windowsRun();
  const run = async (cmd, args) => {
    if (cmd === "schtasks.exe" && args[0] === "/Run") throw new Error("task timeout");
    return fake.run(cmd, args);
  };
  const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir }, { run, user: "HOME\\x" });
  assert.equal(r.ok, false);
  assert.match(r.error, /task timeout/);
  assert.equal(fs.existsSync(path.join(dataDir, "start-constellation.cmd")), false);
});

test("Windows launcher and XML writes are atomic members of the install transaction", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  for (const failedName of ["start-constellation.cmd", "constellation-task.xml"]) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-write-tx-"));
    const launcher = path.join(dataDir, "start-constellation.cmd");
    const xml = path.join(dataDir, "constellation-task.xml");
    const oldLauncher = Buffer.from([0xff, 0x00, 0x41]);
    const oldXml = Buffer.from([0xfe, 0x42, 0x00]);
    fs.writeFileSync(launcher, oldLauncher);
    fs.writeFileSync(xml, oldXml);
    const io = Object.create(fs);
    let injected = false;
    io.renameSync = (from, to) => {
      if (!injected && path.basename(to) === failedName) {
        injected = true;
        throw new Error(`${failedName} replace failed`);
      }
      return fs.renameSync(from, to);
    };
    const fake = windowsRun({ taskXml: VALID_TASK_XML, firewallExists: true });
    const result = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir },
      { run: fake.run, user: "HOME\\x", fs: io });
    assert.equal(result.ok, false);
    assert.match(result.error, new RegExp(`${failedName} replace failed`));
    assert.deepEqual(fs.readFileSync(launcher), oldLauncher);
    assert.deepEqual(fs.readFileSync(xml), oldXml);
    assert.deepEqual(fs.readdirSync(dataDir).sort(), ["constellation-task.xml", "start-constellation.cmd"]);
  }
});

test("Windows second file write failure removes new files and reports cleanup failures without stopping cleanup", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  for (const failCleanup of [false, true]) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-write-tx-"));
    const io = Object.create(fs);
    let xmlFailed = false;
    io.renameSync = (from, to) => {
      if (!xmlFailed && path.basename(to) === "constellation-task.xml") {
        xmlFailed = true;
        throw new Error("XML replace failed");
      }
      return fs.renameSync(from, to);
    };
    io.unlinkSync = (file) => {
      if (failCleanup && path.basename(file) === "start-constellation.cmd") throw new Error("launcher cleanup failed");
      return fs.unlinkSync(file);
    };
    const result = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir },
      { run: windowsRun().run, user: "HOME\\x", fs: io });
    assert.equal(result.ok, false);
    assert.match(result.error, /XML replace failed/);
    if (failCleanup) assert.match(result.error, /launcher cleanup failed/);
    else assert.deepEqual(fs.readdirSync(dataDir), [], "failed transaction leaves no files or temp residue");
    assert.equal(fs.existsSync(path.join(dataDir, "constellation-task.xml")), false,
      "XML cleanup still runs when launcher cleanup fails");
  }
});

test("Windows firewall failure removes resources created by this attempt", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-tx-"));
  const fake = windowsRun();
  const run = async (cmd, args) => cmd === "netsh" && args.includes("add")
    ? { ok: false, out: "", err: "firewall add failed" } : fake.run(cmd, args);
  const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir }, { run, user: "HOME\\x" });
  assert.equal(r.ok, false);
  assert.equal(fs.existsSync(path.join(dataDir, "start-constellation.cmd")), false);
  assert.equal(fs.existsSync(path.join(dataDir, "constellation-task.xml")), false);
  assert.ok(fake.calls.some((c) => c[0] === "schtasks.exe" && c[1] === "/Delete"));
});

test("Windows readiness rollback removes only newly-created firewall state", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  for (const firewallExists of [false, true]) {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-tx-"));
    const fake = windowsRun({ firewallExists });
    const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir }, { run: fake.run, user: "HOME\\x" });
    assert.equal(r.ok, true);
    const rolled = await r.rollback();
    assert.equal(rolled.ok, true);
    const deletedFirewall = fake.calls.some((c) => c[0] === "netsh" && c.includes("delete"));
    assert.equal(deletedFirewall, !firewallExists);
  }
});

test("Windows rollback attempts every cleanup and reports all failures", async () => {
  const fs = require("fs"), os = require("os"), path = require("path");
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cst-win-tx-"));
  const fake = windowsRun({ cleanupFails: ["/Delete", "firewall delete"] });
  const r = await a.installWindows({ exe: "C:\\x\\brain.exe", env: { A: "1" }, dataDir }, { run: fake.run, user: "HOME\\x" });
  assert.equal(r.ok, true);
  const rolled = await r.rollback();
  assert.equal(rolled.ok, false);
  assert.match(rolled.error, /task/i);
  assert.match(rolled.error, /firewall/i);
  assert.equal(fs.existsSync(path.join(dataDir, "start-constellation.cmd")), false,
    "file cleanup still runs after command cleanup failures");
});
