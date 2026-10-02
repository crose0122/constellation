"use strict";

const { wallUrl, isTrustedLocalUrl } = require("./decide");

async function openWall(openExternal, httpPort = 8484) {
  const url = wallUrl(httpPort);
  if (!isTrustedLocalUrl(url, httpPort)) {
    return { ok: false, error: "Only local Constellation pages can be opened." };
  }
  await openExternal(url);
  return { ok: true };
}

module.exports = { openWall };
