// Constellation Setup — start on boot (V2 CP2, spec A2).
// Linux: a systemd *user* unit (no root) + linger so it runs without a login.
// Windows: a per-user Scheduled Task at logon (no service account, no admin).
// The unit/task text is built by pure functions so tests can pin it exactly.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFile } = require("child_process");
const { DOMParser } = require("@xmldom/xmldom");

const UNIT = "constellation.service";
// Written as the first line of every unit this installer creates. A unit
// without it was made by hand (or by an older setup) and is never overwritten:
// that could silently replace a working install's settings.
const MARKER = "# managed-by: constellation-setup";

function mayWriteUnit(existingText) {
  return existingText == null || String(existingText).startsWith(MARKER);
}
const TASK = "Constellation";
const TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task";

function sh(cmd, args, timeout = 20000) {
  return new Promise((resolve) => execFile(cmd, args, { timeout, windowsHide: true },
    (err, out, errOut) => {
      const stderr = String(errOut || "");
      const launchError = err && (typeof err.code !== "number" || err.killed || err.signal)
        ? String(err.message || err) : "";
      resolve({ ok: !err, code: err ? err.code : 0,
        out: String(out || ""), err: stderr || launchError });
    }));
}

function systemdState(result, query) {
  const value = String(result.out || "").trim();
  const expected = query === "is-enabled"
    ? { enabled: true, disabled: false, "not-found": false }
    : { active: true, inactive: false, "not-found": false };
  if (Object.prototype.hasOwnProperty.call(expected, value)) {
    const successMatches = expected[value] ? result.ok : !result.ok;
    if (successMatches && !String(result.err || "").trim()) return { known: true, value: expected[value], state: value };
  }
  const detail = String(result.err || result.out || `exit ${result.code == null ? "unknown" : result.code}`).trim();
  return { known: false, error: `${query} could not determine prior state: ${detail || "empty response"}` };
}

// systemd quoting: one argument per word, escape backslash/quote, wrap in "".
function sdQuote(s) { return `"${String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`; }

function systemdUnit({ exe, envFile, httpPort = 8484, tlsPort = 8485 }) {
  if (!path.isAbsolute(exe)) throw new Error("backend path must be absolute");
  if (!path.isAbsolute(envFile)) throw new Error("config path must be absolute");
  return [
    MARKER,
    "[Unit]",
    "Description=Constellation — family photos, safe at home",
    "After=network-online.target",
    "Wants=network-online.target",
    // a start-rate limit belongs in [Unit]: systemd silently ignores it in
    // [Service], which used to leave the unit without the protection it names
    "StartLimitIntervalSec=0",
    "",
    "[Service]",
    "Type=simple",
    `EnvironmentFile=${envFile}`,
    `ExecStart=${sdQuote(exe)} constellation --host 0.0.0.0 --port ${httpPort} --tls-port ${tlsPort}`,
    "Restart=always",
    "RestartSec=5",
    // a picture frame must come back no matter how many times it falls over
    "NoNewPrivileges=yes",
    "PrivateTmp=yes",
    "",
    "[Install]",
    "WantedBy=default.target",
    "",
  ].join("\n");
}

// Windows Task Scheduler XML. Runs as the installing user at logon, restarts
// on failure, no time limit, hidden. The config is passed via a wrapper .cmd so
// the XML never embeds secrets or long env strings.
function xmlEsc(s) { return String(s).replace(/[<>&"']/g, (c) =>
  ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" }[c])); }

function windowsTaskXml({ launcher, user }) {
  return `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Constellation — family photos, safe at home</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>${xmlEsc(user)}</UserId></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>${xmlEsc(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <Hidden>true</Hidden>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>${xmlEsc(launcher)}</Command></Exec></Actions>
</Task>
`;
}

// .env → `set KEY=VALUE` lines. Values with cmd metacharacters are refused
// rather than escaped: a library path with `&` or `%` gets a clear error at
// setup instead of a silently broken autostart.
function windowsLauncher({ exe, env, httpPort = 8484, tlsPort = 8485 }) {
  const bad = /[&|<>^%!"\r\n]/;
  const lines = ["@echo off"];
  for (const [k, v] of Object.entries(env)) {
    if (!/^[A-Z0-9_]+$/.test(k)) throw new Error(`bad setting name: ${k}`);
    if (bad.test(String(v))) throw new Error(`the path for ${k} contains a character Windows can't start with: ${v}`);
    lines.push(`set "${k}=${v}"`);
  }
  if (bad.test(exe)) throw new Error("the install path contains characters Windows can't start with");
  lines.push(`"${exe}" constellation --host 0.0.0.0 --port ${httpPort} --tls-port ${tlsPort}`);
  return lines.join("\r\n") + "\r\n";
}

function fileSnapshot(file, io = fs) {
  return io.existsSync(file) ? io.readFileSync(file) : null;
}

let tempSequence = 0;
function atomicWriteFile(file, contents, io = fs) {
  const temp = path.join(path.dirname(file), `.${path.basename(file)}.tmp-${process.pid}-${++tempSequence}`);
  try {
    io.writeFileSync(temp, contents, { flag: "wx" });
    io.renameSync(temp, file);
  } catch (e) {
    let cleanup = null;
    try { if (io.existsSync(temp)) io.unlinkSync(temp); }
    catch (cleanupError) { cleanup = String((cleanupError && cleanupError.message) || cleanupError); }
    throw new Error(String((e && e.message) || e) + (cleanup ? `; temp cleanup: ${cleanup}` : ""));
  }
}

function restoreFile(file, snapshot, errors, label, io = fs) {
  try {
    if (snapshot == null) {
      if (io.existsSync(file)) io.unlinkSync(file);
    } else {
      atomicWriteFile(file, snapshot, io);
    }
  } catch (e) { errors.push(`${label}: ${String((e && e.message) || e)}`); }
}

function rollbackResult(errors) {
  return errors.length ? { ok: false, error: errors.join("; ") } : { ok: true };
}

// A preexisting task is only restorable if its exported definition is a
// well-formed Task Scheduler document. A real XML parser decides that: any
// parser warning, error, or fatal error, a DOCTYPE, or a root other than
// <Task> in the Task Scheduler namespace makes the prior state unknown, and
// the install stops before writing anything it could not undo.
function parseTaskXml(xml) {
  const problems = [];
  let doc;
  try {
    doc = new DOMParser({ onError: (level, message) => { problems.push(`${level}: ${String(message).split("\n")[0]}`); } })
      .parseFromString(xml, "text/xml");
  } catch (e) {
    problems.push(String((e && e.message) || e).split("\n")[0]);
  }
  if (problems.length) return { ok: false, error: problems.join("; ") };
  if (!doc) return { ok: false, error: "no document" };
  for (let n = doc.firstChild; n; n = n.nextSibling) {
    if (n.nodeType === 10) return { ok: false, error: "DOCTYPE is not allowed" };
  }
  const root = doc.documentElement;
  if (!root || root.localName !== "Task" || root.namespaceURI !== TASK_NAMESPACE)
    return { ok: false, error: "root element is not a Task Scheduler <Task>" };
  return { ok: true };
}

function scheduledTaskSnapshot(result) {
  const out = String(result.out || "").replace(/^\ufeff/, "").trim();
  const err = String(result.err || "").trim();
  if (result.ok && !err && out) {
    const parsed = parseTaskXml(out);
    if (parsed.ok) return { known: true, xml: out };
    return { known: false, error: `scheduled task snapshot is not valid task XML: ${parsed.error}` };
  }
  if (!result.ok && !out && err === "ERROR: The system cannot find the file specified.")
    return { known: true, xml: null };
  return { known: false, error: `scheduled task snapshot query was ambiguous: ${err || out || "empty response"}` };
}

// `netsh advfirewall firewall show rule name=Constellation` in English prints
// one block per matching rule followed by "Ok.", or exactly "No rules match
// the specified criteria." Only those exact shapes are known; any extra or
// unrecognized line (access denied, localized text, other rule names, mixed
// present/absent signals), stderr noise, or unexpected exit code is unknown.
const NETSH_FIELDS = new Map([
  ["Enabled", /^(?:Yes|No)$/],
  ["Direction", /^(?:In|Out)$/],
  ["Profiles", /^[A-Za-z,]+$/],
  ["Grouping", /^.*$/],
  ["LocalIP", /^\S+$/],
  ["RemoteIP", /^\S+$/],
  ["Protocol", /^\S+$/],
  ["LocalPort", /^\S+$/],
  ["RemotePort", /^\S+$/],
  ["Edge traversal", /^(?:Yes|No|Defer to application|Defer to user)$/],
  ["Action", /^(?:Allow|Block|Bypass)$/],
]);
const NETSH_ABSENT = "No rules match the specified criteria.";

function netshPresentBlocks(out) {
  const lines = out.split(/\r?\n/).map((l) => l.replace(/\s+$/, ""));
  while (lines.length && lines[lines.length - 1] === "") lines.pop();
  if (lines.pop() !== "Ok.") return false;
  let i = 0, blocks = 0;
  while (i < lines.length) {
    if (lines[i] === "") { i++; continue; }
    const name = /^Rule Name:\s+(.*)$/.exec(lines[i]);
    if (!name || name[1] !== TASK) return false;
    if (!/^-{10,}$/.test(lines[i + 1] || "")) return false;
    i += 2;
    const seen = new Set();
    while (i < lines.length && lines[i] !== "") {
      const m = /^([A-Za-z ]+?):(?:\s+(.*))?$/.exec(lines[i]);
      if (!m || !NETSH_FIELDS.has(m[1]) || seen.has(m[1]) || !NETSH_FIELDS.get(m[1]).test(m[2] || "")) return false;
      seen.add(m[1]);
      i++;
    }
    if (seen.size !== NETSH_FIELDS.size) return false;
    blocks++;
  }
  return blocks > 0;
}

function firewallSnapshot(result) {
  const out = String(result.out || "").trim();
  const err = String(result.err || "").trim();
  const message = [out, err].filter(Boolean).join("\n");
  const code = result.ok ? 0 : result.code;
  if ((code === 0 || code === 1) && ((out === NETSH_ABSENT && !err) || (err === NETSH_ABSENT && !out)))
    return { known: true, existed: false };
  if (result.ok && code === 0 && !err && netshPresentBlocks(out))
    return { known: true, existed: true };
  return { known: false, error: `firewall snapshot query was ambiguous: ${message || "empty response"}` };
}

async function installLinux({ exe, envFile }, runtime = {}) {
  const rawRun = runtime.run || sh;
  const run = async (...args) => {
    try { return await rawRun(...args); }
    catch (e) { return { ok: false, out: "", err: String((e && e.message) || e) }; }
  };
  const home = runtime.home || os.homedir();
  const user = runtime.user || os.userInfo().username;
  const dir = path.join(home, ".config", "systemd", "user");
  const unitPath = path.join(dir, UNIT);
  const existing = fileSnapshot(unitPath);
  if (!mayWriteUnit(existing == null ? null : existing.toString("utf8"))) {
    return { ok: false, error: `a Constellation service set up by hand already exists (${unitPath}); leaving it alone` };
  }
  const enabled = systemdState(await run("systemctl", ["--user", "is-enabled", UNIT]), "is-enabled");
  if (!enabled.known) return { ok: false, error: enabled.error };
  const active = systemdState(await run("systemctl", ["--user", "is-active", UNIT]), "is-active");
  if (!active.known) return { ok: false, error: active.error };
  const priorEnabled = enabled.value;
  const priorActive = active.value;
  const restoreInstalledUnit = async (stopCurrent) => {
    const errors = [];
    if (stopCurrent) {
      const disabled = await run("systemctl", ["--user", "disable", "--now", UNIT]);
      if (!disabled.ok) errors.push(`disable unit: ${disabled.err.trim() || "command failed"}`);
    }
    restoreFile(unitPath, existing, errors, "restore unit");
    const reload = await run("systemctl", ["--user", "daemon-reload"]);
    if (!reload.ok) errors.push(`daemon-reload: ${reload.err.trim() || "command failed"}`);
    if (enabled.state !== "not-found") {
      const enabled = await run("systemctl", ["--user", priorEnabled ? "enable" : "disable", UNIT]);
      if (!enabled.ok) errors.push(`restore ${priorEnabled ? "enabled" : "disabled"} unit: ${enabled.err.trim() || "command failed"}`);
    }
    if (enabled.state !== "not-found" && active.state !== "not-found") {
      const active = await run("systemctl", ["--user", priorActive ? "start" : "stop", UNIT]);
      if (!active.ok) errors.push(`restore ${priorActive ? "active" : "inactive"} unit: ${active.err.trim() || "command failed"}`);
    }
    return rollbackResult(errors);
  };
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(unitPath, systemdUnit({ exe, envFile }));
  // Verify BEFORE enabling: systemd-analyze warns about an unknown key even
  // when it exits 0, and a warned unit can mean a protection that never runs
  // (StartLimitIntervalSec in the wrong section did exactly that). A silent
  // verify is the only "yes".
  const verify = await run("systemd-analyze", ["verify", unitPath]);
  if (!verify.ok || verify.err.trim() !== "") {
    const errors = [];
    restoreFile(unitPath, existing, errors, "restore unit");
    return { ok: false, error: `systemd-analyze verify did not pass: ${verify.err.trim() || "exit nonzero"}` +
      (errors.length ? `; rollback failed: ${errors.join("; ")}` : "") };
  }
  const steps = [
    ["systemctl", ["--user", "daemon-reload"]],
    ["systemctl", ["--user", "enable", "--now", UNIT]],
  ];
  for (const [c, a] of steps) {
    const r = await run(c, a);
    if (!r.ok) {
      let errors = [];
      if (c === "systemctl" && a.includes("enable")) {
        const restored = await restoreInstalledUnit(true);
        if (!restored.ok) errors = [restored.error];
      } else {
        restoreFile(unitPath, existing, errors, "restore unit");
        const reload = await run("systemctl", ["--user", "daemon-reload"]);
        if (!reload.ok) errors.push(`daemon-reload: ${reload.err.trim() || "command failed"}`);
      }
      return { ok: false, error: `${c} ${a.join(" ")}: ${r.err.trim()}` +
        (errors.length ? `; rollback failed: ${errors.join("; ")}` : "") };
    }
  }
  // linger = keep running with nobody logged in (a server in a closet). Best
  // effort: it needs polkit on some distros; without it we still start at login.
  const linger = await run("loginctl", ["enable-linger", user]);
  const rollback = async () => restoreInstalledUnit(true);
  return { ok: true, linger: linger.ok, unit: unitPath, started: true,
    startsOnLogin: true, startsOnBoot: linger.ok, rollback };
}

async function installWindows({ exe, env, dataDir }, runtime = {}) {
  const io = runtime.fs || fs;
  const rawRun = runtime.run || sh;
  const runCommand = async (...args) => {
    try { return await rawRun(...args); }
    catch (e) { return { ok: false, out: "", err: String((e && e.message) || e) }; }
  };
  const launcher = path.join(dataDir, "start-constellation.cmd");
  const previousLauncher = fileSnapshot(launcher, io);
  const xmlPath = path.join(dataDir, "constellation-task.xml");
  const previousXml = fileSnapshot(xmlPath, io);
  const previousTask = scheduledTaskSnapshot(
    await runCommand("schtasks.exe", ["/Query", "/TN", TASK, "/XML"]));
  if (!previousTask.known) return { ok: false, error: previousTask.error };
  const taskXml = previousTask.xml;
  const previousFirewall = firewallSnapshot(
    await runCommand("netsh", ["advfirewall", "firewall", "show", "rule", "name=Constellation"]));
  if (!previousFirewall.known) return { ok: false, error: previousFirewall.error };
  const firewallExisted = previousFirewall.existed;
  let taskChanged = false;
  let firewallAdded = false;
  let rolledBack = false;

  const rollback = async () => {
    if (rolledBack) return { ok: true };
    rolledBack = true;
    const errors = [];
    if (firewallAdded) {
      const r = await runCommand("netsh", ["advfirewall", "firewall", "delete", "rule", "name=Constellation"]);
      if (!r.ok) errors.push(`firewall cleanup: ${r.err.trim()}`);
    }
    if (taskChanged) {
      if (taskXml != null) {
        const restoreXml = path.join(dataDir, ".constellation-task-restore.xml");
        try {
          atomicWriteFile(restoreXml, Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from(taskXml, "utf16le")]), io);
          const r = await runCommand("schtasks.exe", ["/Create", "/TN", TASK, "/XML", restoreXml, "/F"]);
          if (!r.ok) errors.push(`task restore: ${r.err.trim()}`);
        } catch (e) { errors.push(`task restore: ${String((e && e.message) || e)}`); }
        finally { try { if (io.existsSync(restoreXml)) io.unlinkSync(restoreXml); } catch (e) {
          errors.push(`task restore file cleanup: ${String((e && e.message) || e)}`); } }
      } else {
        const r = await runCommand("schtasks.exe", ["/Delete", "/TN", TASK, "/F"]);
        if (!r.ok) errors.push(`task cleanup: ${r.err.trim()}`);
      }
    }
    restoreFile(launcher, previousLauncher, errors, "restore launcher", io);
    restoreFile(xmlPath, previousXml, errors, "restore task XML", io);
    return rollbackResult(errors);
  };

  const user = runtime.user || `${process.env.USERDOMAIN || os.hostname()}\\${os.userInfo().username}`;
  // UTF-16LE with BOM, as schtasks expects for the encoding the XML declares
  try {
    atomicWriteFile(launcher, windowsLauncher({ exe, env }), io);
    atomicWriteFile(xmlPath, Buffer.concat([Buffer.from([0xff, 0xfe]),
      Buffer.from(windowsTaskXml({ launcher, user }), "utf16le")]), io);
  } catch (e) {
    const rb = await rollback();
    return { ok: false, error: `startup file write failed: ${String((e && e.message) || e)}` +
      (!rb.ok ? `; rollback failed: ${rb.error}` : "") };
  }
  const create = await runCommand("schtasks.exe", ["/Create", "/TN", TASK, "/XML", xmlPath, "/F"]);
  taskChanged = create.ok;
  if (!create.ok) {
    const rb = await rollback();
    return { ok: false, error: `schtasks /Create: ${create.err.trim()}` + (!rb.ok ? `; rollback failed: ${rb.error}` : "") };
  }
  // Startup must be verified, not assumed: a task that exists but refuses to
  // start is a picture frame that stays dark after the next reboot.
  const run = await runCommand("schtasks.exe", ["/Run", "/TN", TASK]);
  if (!run.ok) {
    const rb = await rollback();
    return { ok: false, error: `schtasks /Run: ${run.err.trim()}` + (!rb.ok ? `; rollback failed: ${rb.error}` : "") };
  }
  // Windows Firewall: private networks only (the family LAN), never public.
  if (!firewallExisted) {
    const fw = await runCommand("netsh", ["advfirewall", "firewall", "add", "rule", "name=Constellation",
      "dir=in", "action=allow", "protocol=TCP", "localport=8484,8485", "profile=private"]);
    firewallAdded = fw.ok;
    if (!fw.ok) {
      const rb = await rollback();
      return { ok: false, error: `firewall rule failed: ${fw.err.trim()}` + (!rb.ok ? `; rollback failed: ${rb.error}` : "") };
    }
  }
  return { ok: true, task: TASK, started: true, startsOnLogin: true,
    startsOnBoot: false, rollback };
}

function parseEnvFile(text) {
  const env = {};
  for (const line of String(text).split(/\r?\n/)) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (m) env[m[1]] = m[2];
  }
  return env;
}

async function install({ exe, envFile, dataDir }) {
  if (process.platform === "win32") {
    return installWindows({ exe, dataDir, env: parseEnvFile(fs.readFileSync(envFile, "utf8")) });
  }
  if (process.platform === "linux") return installLinux({ exe, envFile });
  return { ok: false, error: "This system isn't supported yet (Linux and Windows are)." };
}

module.exports = { systemdUnit, windowsTaskXml, windowsLauncher, parseEnvFile, install, installLinux, installWindows, mayWriteUnit,
  scheduledTaskSnapshot, firewallSnapshot, parseTaskXml, TASK_NAMESPACE, UNIT, TASK, MARKER };
