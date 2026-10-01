"use strict";

const { WALL_URL, isTrustedLocalUrl } = require("./decide");

async function openWall(openExternal) {
  if (!isTrustedLocalUrl(WALL_URL)) {
    return { ok: false, error: "Only local Constellation pages can be opened." };
  }
  await openExternal(WALL_URL);
  return { ok: true };
}

module.exports = { openWall };
