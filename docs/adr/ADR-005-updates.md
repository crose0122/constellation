# ADR-005 — Update channel and signing (CP10)

**Status:** Proposed (the build agent, 2026-09-23). Reviewer: the reviewer.
**Spec:** V2 §3.4 (A4), §9, gate 9.

## Decision
1. **Channel.** A static HTTPS manifest (for example, GitHub Releases on the public repo) listing `{version, platform, sha256, size, url, min_from}`.
   - The manifest is **signed with minisign/Ed25519**. The public key is compiled into the installer.
   - An unsigned or badly signed manifest is ignored and logged.
   - This is the **only** outbound call Constellation ever makes, and it is **opt-in in the wizard** (on by default, with a plain-language note that it only fetches updates and sends nothing).
   - The egress test (invariant 1) whitelists exactly this host, and only while the updater runs.
2. **Window.** Download at any time. **Install only between 03:00 and 05:00 local time** (configurable), and only at a slideshow boundary: the display clients report "between memories", and the server waits up to 10 minutes for that.
3. **Apply.**
   - Stage the new version next to the current one.
   - Take a database backup.
   - Switch the symlink (Linux) or run the NSIS silent upgrade (Windows).
   - Run a health check against `/health` and the pipeline dry-run.
4. **Rollback.** If the health check fails, switch back and restore the pre-update database backup. Tell an adult in plain words the next morning.
5. **Manual.** "Check for updates now" sits behind the PIN.

## Consequences
- Invariant 1 gets one audited exception: user-visible, opt-in, send-nothing. **Approved by the founder 2026-09-23.**
