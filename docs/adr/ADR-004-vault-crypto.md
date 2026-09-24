# ADR-004 — Per-user vault crypto (CP8)

**Status:** Proposed; the founder decided Windows = age folders, v1 migration = manual sort (2026-09-23). Reviewer: the reviewer.
**Spec:** V2 §7 (D1–D5), gates 6 and 7. **Invariant:** 3 (the vault wall).

## Context
v1 has **one** LUKS container with one shared passphrase ("both keyholders present", `deploy/vault-ceremony.sh`), plus a `dir` mode for installs without LUKS. V2 requires per-user secrets with optional sharing, and the family PIN must never open a vault.

## Decision
1. **One LUKS2 container per adult.** Each is created at setup and mounted only on demand. The existing idle auto-close timer applies to each one.
2. **Keys.**
   - Each adult's passphrase is their own. A **recovery key** is generated at setup, shown once, and saved to Vaultwarden by the adult (the installer offers a "copy to Vaultwarden" handoff).
   - There is a spoken "lost = unrecoverable" warning and a required checkbox.
   - Sharing is optional at setup: add the other adult's passphrase as a second LUKS keyslot on your container. It can be revoked by removing that keyslot.
3. **Routing without the key.**
   - At ingest, a photo flagged for the vault has to be written into a container that may be **closed**.
   - Each container has an age/X25519 **inbox public key**. Flagged photos are encrypted to that key and dropped into `vault-inbox/<person>/` on the normal disk.
   - The only copy is the encrypted one.
   - When the owner next opens their vault, the inbox is decrypted into the container and wiped.
   - So routing never needs the passphrase, and nothing is stored in plaintext.
4. **Windows / non-LUKS.** VeraCrypt isn't bundled. On Windows, each vault is an **age-encrypted archive directory**: the same inbox model, decrypted into memory to view. `dir` mode remains available only when explicitly chosen, with a warning ("not encrypted") in the wizard.
5. **Migrating the v1 shared vault.** An adult opens the old container once and chooses whose vault each existing item goes to (bulk by subfolder: `parent2` goes to that adult, `review` goes to the release queue). The old container is closed and kept, never deleted automatically.
6. **The wall test** (invariant 3) is extended to prove:
   - the inbox is encrypted (no plaintext image ever sits on the normal disk);
   - no derivative exists for inbox or vault items.

## Consequences
- The ingest path needs the age library (`pyrage`) and a tested inbox round-trip.
- Losing both the passphrase and the recovery key means that adult's vault is gone. That's the documented, spoken trade-off.

## Open for the founder
- Confirm Windows vaults as age archives (no VeraCrypt).
- Confirm the migration mapping for the existing `parent2` / `other` / `review` subfolders.
