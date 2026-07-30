# Wall Page Modes — Blank Wall + Constellation Portal Overlay

## Context

Constellation already has a `/wall` route (`scripts/memoryvault/brain/static/wall.html`) that renders a salon-style gallery of antique and timber frames over a living starfield backdrop (`/ambient?calm=1`). The frames hold rotating photos from the family library.

This design adds two explicit wall modes that the user can toggle:

1. **Blank Wall** — a quiet, painted wall with portraits hung like framed art; the starfield is hidden.
2. **Constellation Overlay** — the same wall, but each frame becomes a swirling portal/wormhole you peer into, with the family member visible through the vortex.

## Goals

- Give the wall an "at rest" state that feels like a real gallery (Blank Wall).
- Give the wall a "magic" state that feels like the Constellation product identity (Portal Overlay).
- Keep the existing frame layout, photo cycling, and lightbox behavior intact.
- Make the toggle immediate and obvious: one tap, or a URL param, or an idle-timeout fallback.

## User Experience

### Blank Wall mode

- The backdrop is a painted wall texture (plaster, panel, or themed surface) instead of the starfield iframe.
- The same antique/timber frames hang on the wall with the same salon layout.
- Photos rotate inside the frames as they do today.
- Tapping a frame opens the existing lightbox.
- The mood is quiet, domestic, gallery-like.

### Constellation Overlay mode

- A starfield / nebula layer fades in behind the frames (reusing `/ambient?calm=1`).
- Each frame's picture window becomes a **portal** — a subtle swirling vortex rendered inside the frame opening.
- The family member / photo is still visible, but distorted as if seen through the portal.
- Stars drift across the whole surface; shooting stars occasionally trace between frames.
- Tapping a frame opens the lightbox with a "fly through" transition.
- The transition between modes is a slow cross-fade (3–5 seconds) with subtle ambient sound.

### Interaction

- A small, persistent icon in a corner indicates the current mode (frame icon vs. star/portal icon).
- Tapping the background toggles modes.
- After a period of inactivity, the wall can default to either mode based on a setting (`blank` or `portal`).
- On a phone / handheld, the same frames scroll vertically; the portal shader runs at reduced quality.

## Implementation Notes

### Reuse existing pieces

- Frame HTML/CSS from `scripts/memoryvault/brain/static/wall.html` (`.aframe`, `.frame`, `.win`, `.aframe .art`).
- Frame art generation from `scripts/tools/make_frames.py` and `frames.css`.
- Photo pool and slot cycling from `wall.html` (`loadPool`, `fillSlot`, `tick`).
- Lightbox and actions from `wall.html` (`openLightbox`, `closeLightbox`, etc.).
- Starfield from `index.html` + `brain.js` via `/ambient?calm=1`.

### New pieces

- **Mode state** in `wall.html`:
  - URL params: `?mode=blank|portal`
  - Default: `portal` (matches current behavior) or user setting.
  - Toggle function swaps the backdrop and portal effects.
- **Blank backdrop**:
  - A new CSS background layer (`#wall.mode-blank`) with a painted texture.
  - Hide the `#sky` iframe in blank mode.
- **Portal effect**:
  - Add a shader/CSS animation inside `.win` when in portal mode.
  - The shader is applied to a container that sits between the photo and the frame art.
  - Theme-matched vortex styles: warm gold (classic), blue warp (sci-fi), water spiral (ocean), etc.
  - Fallback for low-end devices: pre-rendered looping WebM under the frames at reduced opacity.
- **Transition**:
  - CSS cross-fade on the backdrop.
  - Optional theme-matched ambient sound (gentle chimes, warp hum, ocean swell).
- **Mode indicator**:
  - Small corner button with `aria-label` to toggle and show current mode.

### Performance

- Must stay at 30fps on a low-end mini-PC or Fire HD tablet.
- Portal effect is shader/CSS, not a full video loop.
- Photos are cached; the shader is applied to the frame window, not re-decoded per frame.
- `?lite` should also downgrade portal effects.

## Suggested File Changes

- `scripts/memoryvault/brain/static/wall.html`
  - Add mode toggle UI.
  - Add `#wall.mode-blank` and `#wall.mode-portal` CSS.
  - Add portal shader DOM/CSS inside `.win`.
  - Add `toggleMode()`, `setMode(mode)`.
- `scripts/memoryvault/brain/static/brain.css` or a new `wall.css`
  - Portal vortex animations and blank-wall backdrop styles.
- `scripts/tools/make_frames.py` (optional)
  - Generate portal-themed frame variants if needed.
- `scripts/memoryvault/brain/server.py` (optional)
  - Accept a `mode` query param for deep-linking.

## Acceptance Criteria

- [ ] `/wall?mode=blank` shows a painted wall with hung frames and no starfield.
- [ ] `/wall?mode=portal` shows the same frames with a swirling portal effect inside each window and a starfield backdrop.
- [ ] Tapping the background toggles modes with a smooth 3–5 second cross-fade.
- [ ] Tapping a frame still opens the existing lightbox; closing it returns to the wall.
- [ ] The wall still cycles photos, respects `?lite`, and stays above 30fps on a budget TV box.
- [ ] The mode preference is remembered per device (localStorage) or configurable by the family.

## Out of Scope

- New frame art beyond existing antique/timber frames.
- Sound effects beyond optional ambient transition sounds.
- Multi-user sync of mode preference.
