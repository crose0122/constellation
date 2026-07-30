#!/usr/bin/env python3
"""Generate an ornate gilded frame SVG (baroque egg-and-dart molding, bead
rows, corner acanthus volutes). Emits a full-frame preview and a 9-slice
border-image tile."""
import os
import math

GOLD_DEFS = """
<defs>
  <linearGradient id="gold" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#6e4d15"/>
    <stop offset="0.18" stop-color="#f6e4a6"/>
    <stop offset="0.42" stop-color="#c69a34"/>
    <stop offset="0.6" stop-color="#8a6417"/>
    <stop offset="0.8" stop-color="#f2dd9b"/>
    <stop offset="1" stop-color="#6e4d15"/>
  </linearGradient>
  <linearGradient id="goldDark" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#4a330d"/>
    <stop offset="0.5" stop-color="#8a6417"/>
    <stop offset="1" stop-color="#3a280a"/>
  </linearGradient>
  <radialGradient id="bead" cx="0.35" cy="0.3" r="0.8">
    <stop offset="0" stop-color="#fbeebb"/>
    <stop offset="0.6" stop-color="#c69a34"/>
    <stop offset="1" stop-color="#6e4d15"/>
  </radialGradient>
</defs>
"""


def volute(cx, cy, s, rot):
    """A corner acanthus: a spiral volute with a couple of leaf lobes.
    s = scale, rot = degrees."""
    pts = []
    for i in range(0, 220, 8):
        a = math.radians(i)
        r = s * (0.06 + 0.010 * i)
        pts.append((cx + r * math.cos(a), cy - r * math.sin(a)))
    d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    # leaf lobes (two teardrop curves fanning off the corner)
    leaves = ""
    for k, ang in enumerate((35, 70, 105)):
        a = math.radians(ang)
        lx, ly = cx + s * 0.9 * math.cos(a), cy - s * 0.9 * math.sin(a)
        mx, my = cx + s * 0.45 * math.cos(a - 0.25), cy - s * 0.45 * math.sin(a - 0.25)
        leaves += (f'<path d="M {cx:.1f},{cy:.1f} Q {mx:.1f},{my:.1f} {lx:.1f},{ly:.1f} '
                   f'Q {cx+ s*0.5*math.cos(a+0.3):.1f},{cy - s*0.5*math.sin(a+0.3):.1f} '
                   f'{cx:.1f},{cy:.1f} Z" fill="url(#gold)" '
                   f'stroke="#5a3d0e" stroke-width="1.2"/>')
    return (f'<g transform="rotate({rot} {cx} {cy})">{leaves}'
            f'<path d="{d}" fill="none" stroke="url(#gold)" stroke-width="{s*0.16:.1f}" '
            f'stroke-linecap="round"/>'
            f'<path d="{d}" fill="none" stroke="#5a3d0e" stroke-width="1"/></g>')


def egg_and_dart(x0, y0, length, horizontal, band):
    """A run of egg-and-dart along an edge segment."""
    out = []
    n = max(2, int(length / (band * 1.5)))
    step = length / n
    egg_r = band * 0.24
    for i in range(n):
        c = x0 + step * (i + 0.5) if horizontal else x0
        d = y0 if horizontal else y0 + step * (i + 0.5)
        cx, cy = (c, y0) if horizontal else (x0, d)
        # egg: an ellipse in a shell
        out.append(f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{egg_r:.1f}" '
                   f'ry="{egg_r*1.25:.1f}" fill="url(#gold)" stroke="#5a3d0e" '
                   f'stroke-width="1.3"/>')
        out.append(f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{egg_r*1.35:.1f}" '
                   f'ry="{egg_r*1.55:.1f}" fill="none" stroke="url(#goldDark)" '
                   f'stroke-width="2.4"/>')
        # dart between eggs
        dx = x0 + step * (i + 1) if horizontal else x0
        dy = y0 if horizontal else y0 + step * (i + 1)
        if horizontal:
            out.append(f'<path d="M {dx:.1f},{dy-egg_r:.1f} L {dx-3:.1f},{dy+egg_r:.1f} '
                       f'L {dx+3:.1f},{dy+egg_r:.1f} Z" fill="url(#goldDark)"/>')
        else:
            out.append(f'<path d="M {dx-egg_r:.1f},{dy:.1f} L {dx+egg_r:.1f},{dy-3:.1f} '
                       f'L {dx+egg_r:.1f},{dy+3:.1f} Z" fill="url(#goldDark)"/>')
    return "".join(out)


def beads(x0, y0, length, horizontal, r, gap):
    out = []
    n = int(length / gap)
    for i in range(n + 1):
        cx = x0 + gap * i if horizontal else x0
        cy = y0 if horizontal else y0 + gap * i
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="url(#bead)" '
                   f'stroke="#5a3d0e" stroke-width="0.6"/>')
    return "".join(out)


def frame_svg(W, H, band, preview=False):
    inner = band
    ix0, iy0, ix1, iy1 = band, band, W - band, H - band
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">', GOLD_DEFS]
    if preview:
        parts.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#0c0a08"/>')
        parts.append(f'<rect x="{ix0}" y="{iy0}" width="{ix1-ix0}" height="{iy1-iy0}" fill="#555"/>')
    # molding base: outer rect minus inner opening (even-odd)
    parts.append(f'<path fill-rule="evenodd" fill="url(#gold)" d="'
                 f'M0,0 H{W} V{H} H0 Z M{ix0},{iy0} H{ix1} V{iy1} H{ix0} Z"/>')
    # bevel ridges
    parts.append(f'<rect x="3" y="3" width="{W-6}" height="{H-6}" fill="none" '
                 f'stroke="#fff4cf" stroke-opacity="0.35" stroke-width="2"/>')
    parts.append(f'<rect x="{ix0}" y="{iy0}" width="{ix1-ix0}" height="{iy1-iy0}" '
                 f'fill="none" stroke="#c9a542" stroke-width="3"/>')
    parts.append(f'<rect x="1" y="1" width="{W-2}" height="{H-2}" fill="none" '
                 f'stroke="#3a280a" stroke-width="2"/>')
    # egg-and-dart along a mid-band line, insetting past the corners
    m = band * 0.5
    corner = band * 0.8
    parts.append(egg_and_dart(ix0 + corner, m, W - 2*(ix0+corner), True, band))
    parts.append(egg_and_dart(ix0 + corner, H - m, W - 2*(ix0+corner), True, band))
    parts.append(egg_and_dart(m, iy0 + corner, H - 2*(iy0+corner), False, band))
    parts.append(egg_and_dart(W - m, iy0 + corner, H - 2*(iy0+corner), False, band))
    # bead row just inside the opening
    parts.append(beads(ix0 + 6, iy0 - 4, ix1 - ix0 - 12, True, band*0.10, band*0.34))
    parts.append(beads(ix0 + 6, iy1 + 4, ix1 - ix0 - 12, True, band*0.10, band*0.34))
    parts.append(beads(ix0 - 4, iy0 + 6, iy1 - iy0 - 12, False, band*0.10, band*0.34))
    parts.append(beads(ix1 + 4, iy0 + 6, iy1 - iy0 - 12, False, band*0.10, band*0.34))
    # corner acanthus volutes
    s = band * 0.9
    parts.append(volute(band*0.62, band*0.62, s, 0))
    parts.append(volute(W - band*0.62, band*0.62, s, 90))
    parts.append(volute(W - band*0.62, H - band*0.62, s, 180))
    parts.append(volute(band*0.62, H - band*0.62, s, 270))
    parts.append('</svg>')
    return "".join(parts)


if __name__ == "__main__":
    import sys
    svg = frame_svg(600, 480, 70, preview=("preview" in sys.argv))
    open(os.environ.get("FRAME_SVG_OUT", "frame.svg"), "w").write(svg)
    print(len(svg), "bytes")
