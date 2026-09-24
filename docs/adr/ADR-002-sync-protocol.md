# ADR-002 — Phone sync protocol (CP5)

**Status:** Proposed (the build agent, 2026-09-23). Reviewer: the reviewer.
**Spec:** V2 §5 (C1–C5), gates 4 and 5. **Invariants:** 1 (LAN-only), 4 (backup of record), 5 (quiet).

## Decision
1. **Device credential.**
   - Pairing uses a QR code or a 6-digit code shown on the server. An adult approves it behind the family PIN.
   - The device gets a per-device random credential. Only its hash is stored on the server. It is bound to one **person** (CP7) and one **device**.
   - An adult can revoke it from the health page.
   - The family PIN is never stored on the phone.
2. **Transport.** HTTPS through the family CA (CP1), LAN only. The client only syncs on Wi-Fi and only when it can reach the server's LAN address. The server refuses non-private source addresses.
3. **Upload** (idempotent and resumable).
   - `POST /api/sync/v1/check` with `[{sha256, size, taken_at}]` returns the hashes the server hasn't got yet.
   - `PUT /api/sync/v1/blob/<sha256>` streams the bytes, with `Content-Range` chunks for large files.
   - The server hashes what it receives and rejects a mismatch. The same hash twice is a no-op success.
   - A new blob is recorded as a **version** of that asset on that device (asset id from MediaStore or PhotoKit). An edit creates a new version, and the original is kept.
4. **Deletes.** The protocol has **no delete verb.** Deleting a photo on the phone is never sent to the server (invariant 4). Junk removal on the server is an adult action behind the PIN, and never a side effect of sync.
5. **Free up space.**
   - The app asks `POST /api/sync/v1/verified` with its local hashes.
   - The server returns only the hashes it has stored **and** confirmed with a read-back.
   - The app then offers to delete only those, through the OS's own delete dialog.
6. **Ownership.** Every uploaded blob is owned by the device's person. Kids' devices need opt-in (CP7). Their uploads land on the kid's shelf, and the parents can see the upload log (spec C6).
7. **Quiet.** No per-upload notifications. The only notice is an optional daily digest, off by default.

## Consequences
- The server needs asset and version tables and person-owned blobs, so CP7's schema must land first (or together with this).
- iOS background limits are real: the app catches up whenever it's opened, and the app copy says so honestly.

## Rejected
- **Two-way mirroring:** one phone wipe could delete the family archive (interview C4).
- **A WebDAV or Immich-compatible API:** a larger attack surface, with delete semantics built in.
