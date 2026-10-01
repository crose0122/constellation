"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const manifest = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
const lock = JSON.parse(fs.readFileSync(path.join(root, "package-lock.json"), "utf8"));

function tuple(version) {
  const match = /^(\d+)\.(\d+)\.(\d+)/.exec(version);
  assert.ok(match, `expected a stable semantic version, got ${version}`);
  return match.slice(1).map(Number);
}

function greaterThan(version, vulnerableMaximum) {
  const left = tuple(version);
  const right = tuple(vulnerableMaximum);
  for (let i = 0; i < 3; i += 1) {
    if (left[i] !== right[i]) return left[i] > right[i];
  }
  return false;
}

function resolved(name) {
  const entry = lock.packages[`node_modules/${name}`];
  assert.ok(entry, `${name} must be present in the lockfile`);
  return entry.version;
}

test("runtime and artifact tooling resolve outside audited vulnerable ranges", () => {
  assert.equal(manifest.devDependencies.electron, "44.4.5");
  assert.equal(manifest.devDependencies["electron-builder"], "26.15.3");
  assert.equal(resolved("electron"), "44.4.5");
  assert.equal(resolved("electron-builder"), "26.15.3");
  assert.ok(greaterThan(resolved("app-builder-lib"), "26.14.0"));
  assert.ok(greaterThan(resolved("builder-util-runtime"), "9.6.0"));
  assert.ok(greaterThan(resolved("tar"), "7.5.20"));
  assert.ok(greaterThan(resolved("js-yaml"), "4.3.1"));
  assert.equal(lock.packages["node_modules/extract-zip"], undefined,
    "the unpatched extract-zip package must not remain in Electron's download path");
});

test("declared Node floor satisfies Electron and CI uses that major", () => {
  assert.equal(manifest.engines.node, ">=22.12.0");
  const workflow = fs.readFileSync(path.join(root, "..", ".github", "workflows", "tests.yml"), "utf8");
  assert.match(workflow, /node-version:\s*["']22\.12\.0["']/);
});

test("packaged Electron smoke attempts the shipped sandbox policy first", () => {
  const smoke = fs.readFileSync(path.join(root, "test", "e2e-wizard.js"), "utf8");
  const firstLaunch = smoke.indexOf("electron.launch({ executablePath: exe, env })");
  const fallback = smoke.indexOf('args: ["--no-sandbox"]');
  assert.ok(firstLaunch !== -1, "smoke test must first launch without disabling Chromium sandboxing");
  assert.ok(fallback > firstLaunch, "no-sandbox may only be a later host-compatibility fallback");
});

test("packaged Electron smoke only falls back to --no-sandbox for sandbox launch errors", () => {
  const { isSandboxLaunchError } = require("./e2e-wizard.js");
  const sandboxFailures = [
    "The SUID sandbox helper binary was found, but is not configured correctly. " +
      "Rather than run without sandboxing I'm aborting now. You need to make sure that " +
      "/tmp/x/chrome-sandbox is owned by root and has mode 4755.",
    "FATAL:setuid_sandbox_host.cc(163)] The SUID sandbox helper binary was found",
    "No usable sandbox! Update your kernel or see https://chromium.googlesource.com/...",
    "FATAL:zygote_host_impl_linux.cc] Check failed: . : Invalid argument (22) " +
      "credentials.cc: Failed to create user namespace",
    "Failed to move to new namespace: PID namespaces supported, Network namespace supported, " +
      "but failed: errno = Operation not permitted",
  ];
  for (const message of sandboxFailures) {
    assert.equal(isSandboxLaunchError(new Error(message)), true, message);
  }
  for (const message of [
    "spawn /x/dist/linux-unpacked/constellation-setup ENOENT",
    "Timeout 30000ms exceeded while waiting for event \"window\"",
    "Process failed to launch!",
    "Cannot find module 'playwright'",
    "",
  ]) {
    assert.equal(isSandboxLaunchError(new Error(message)), false, message);
  }
  assert.equal(isSandboxLaunchError(undefined), false);
  assert.equal(isSandboxLaunchError("No usable sandbox!"), true);
});

test("packaged Electron smoke removes only its own temporary HOME", () => {
  const smoke = fs.readFileSync(path.join(root, "test", "e2e-wizard.js"), "utf8");
  assert.match(smoke, /finally\s*\{[\s\S]*fs\.rmSync\(home,\s*\{\s*recursive:\s*true,\s*force:\s*true\s*\}\)/,
    "the mkdtemp HOME must be removed in a finally block");
  assert.doesNotMatch(smoke, /readdirSync\(\s*["'`]\/tmp/, "must not sweep other /tmp/cst-home-* directories");
  assert.doesNotMatch(smoke, /rmSync\(\s*["'`]\/tmp/, "must not remove a hard-coded /tmp path");
  const requireOnly = require("child_process").spawnSync(process.execPath,
    ["-e", "require(process.argv[1]); console.log('loaded')", path.join(root, "test", "e2e-wizard.js")],
    { encoding: "utf8", timeout: 10000 });
  assert.equal(requireOnly.status, 0, requireOnly.stderr);
  assert.equal(requireOnly.stdout.trim(), "loaded", "requiring the smoke module must not launch Electron");
});

test("Electron Builder 26 accepts the checked-in packaging configuration", async () => {
  const { getConfig, validateConfiguration } = require("app-builder-lib/out/util/config/config");
  const builderUtil = require("builder-util");
  const originalStream = builderUtil.log.stream;
  builderUtil.log.stream = { write() {} };
  try {
    const config = await getConfig(root, null, {});
    await validateConfiguration(config, new builderUtil.DebugLogger(false));
    assert.deepEqual(config.linux.target, ["AppImage"]);
    assert.deepEqual(config.win.target, ["nsis"]);
    assert.equal(config.extraResources.length, 2);
  } finally {
    builderUtil.log.stream = originalStream;
  }
});
