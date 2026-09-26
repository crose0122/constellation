# ADR-001 — Helper protocol (CP4)

**Status:** Proposed (the build agent, 2026-09-23). Reviewer: the reviewer.
**Spec:** V2 §4.1 (B1, B2), gate 3. **Invariants:** 1 (LAN-only), 2 (originals untouched).

## Context
Photos live on several family computers. The server has to find and pull them without the helper ever changing anything on that machine, and without opening a door any other LAN device could use.

## Decision
1. **Shape.** A small, single-purpose daemon for Linux (systemd user unit) and Windows (a scheduled task at logon, running as the user). It runs in the user's session so it only ever sees what that user can see.
2. **Discovery.** It advertises `_constellation-helper._tcp` over mDNS. The TXT record carries only a random `helper_id` and a protocol version. It never carries a hostname, user name or folder list.
3. **Pairing.**
   - At first run, the helper shows a 6-digit code on its own screen.
   - An adult enters that code in the server wizard. The code must be confirmed on the helper's machine.
   - The two sides exchange self-signed certificates over a PAKE-style check (SPAKE2 using the code), then **pin each other**.
   - After pairing, every connection is mutual TLS with those pinned certificates. An unpaired client gets a TLS failure and never sees the API.
4. **Consent.** The user ticks the folders to offer **on the helper's machine**. The server can only ask about folders from that list. Consent is stored locally and can be revoked on the helper at any time.
5. **API: read-only by construction.**
   - `GET /v1/folders` returns the consented roots, with counts and total size.
   - `GET /v1/list?root=<id>&cursor=` pages through files: relative path, size, mtime and a content hash (computed lazily and cached).
   - `GET /v1/file?root=<id>&path=` streams the bytes. It supports `Range`, so interrupted transfers resume.
   - **There are no write, move, rename or delete verbs.** The HTTP router has only these three GET routes, and any other method or path returns 405/404.
   - **Path safety.** A path is resolved against the root with no-follow semantics. A path that leaves the root, is a symlink or hard link out of the root, a device file, or a `..` component is refused with 404. It is never "clamped" into the root.
6. **Server side.** The import engine (CP4) records a per-file cursor and hash, so an import can survive a reboot on either end. Anything already present by hash is skipped.

## Consequences
- Tests have to prove the helper cannot write, rename or delete anything (checksums before and after, over the full API surface with fuzzed paths).
- Tests have to prove an unpaired client is refused at the TLS layer.
- One more small binary to package on each OS, reusing the CP2 packaging.

## Rejected
- **SMB/NFS shares:** needs admin setup on every machine, and it's read/write by default.
- **Pushing from a sync client on each PC:** more moving parts, and it breaks "consent once, on that machine".
