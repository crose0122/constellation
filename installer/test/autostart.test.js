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
