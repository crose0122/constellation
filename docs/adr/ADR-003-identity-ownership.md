# ADR-003 — People, ownership and visibility (CP7)

**Status:** Proposed (the build agent, 2026-09-23). Reviewer: the reviewer.
**Spec:** V2 §5.3 (C6), §6, §7. This is a prerequisite for vaults (CP8) and displays (CP9).

## Decision
1. **Tables** (added by migration; existing rows are backfilled):
   - `people(id, display_name, role ∈ {adult, kid}, created_at)`
   - `devices(id, person_id, kind ∈ {phone, display, helper}, label, credential_hash, revoked_at)`
   - `photos.owner_person_id`: NULL means the **family library**, which covers everything already imported (v1 behaviour is unchanged).
   - `photos.visibility ∈ {family, shelf}`. `shelf` means only the owner can see it (plus parents for moderation, per C6).
2. **One query gate.** Every read goes through a single function that takes a *viewer* (a person, a display identity or an anonymous frame) and returns SQL predicates. Code never filters by hand. Two tests enforce this:
   - an architecture test fails if a module under `constellation/` queries `photos` without going through the gate;
   - a permission matrix test covers adult, kid A, kid B, a family display and a personal display against every read route.
3. **Kids.**
   - Sync is off until an adult turns it on for that kid.
   - A kid's uploads default to `shelf`.
   - "Share to family" flips a single photo to `family`, and an audit row is written.
4. **Vault** (CP8) is a separate store, not a visibility value. A vaulted photo has no row in `photos` at all (the v1 wall construction), so no visibility bug can leak it.

## Consequences
- A migration with a tested backfill. The v1 library stays `family` with an owner of NULL.
- Existing routes are migrated onto the gate one at a time, each with its matrix rows.
