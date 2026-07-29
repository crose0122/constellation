#!/usr/bin/env python3
"""Generate the gallery wall's picture frames as CSS border-images.

A reference board of real antique frames sorts
into a handful of profiles that share structure: a molding (gilt, silver,
ebonised), an edge treatment repeated along the rails (beading, egg-and-dart,
fluting, ropework), and a corner ornament (acanthus volutes, rococo scrolls,
shell crests). Rather than hand-tune a base64 blob per style, this builds each
one from those three choices and writes brain/static/frames.css.

Run it after editing a profile:
    python3 tools/make_frames.py

border-image needs the ornament to live in the border band and the middle to
be empty, so every frame is drawn on a 440x440 canvas with an 84px band.
"""
from __future__ import annotations

import base64
import math
import pathlib

SIZE = 440           # canvas
BAND = 84            # border width the CSS slices at
INNER = SIZE - BAND  # inner edge of the molding

OUT = (pathlib.Path(__file__).resolve().parent.parent
       / "memoryvault" / "brain" / "static" / "frames.css")


# ---------------------------------------------------------------- palettes
# Each is a sheen ramp read across the molding: dark valley, bright crown,
# mid, shadowed hollow, second highlight, dark valley. That alternation is
# what reads as carved metal rather than a flat coloured bar.
PALETTES = {
    "gold": ["#6e4d15", "#f6e4a6", "#c69a34", "#8a6417", "#f2dd9b", "#6e4d15"],
    "paleGold": ["#7d6430", "#fdf3d2", "#dcc07a", "#a98b3f", "#f7ecc4", "#7d6430"],
    "silver": ["#4f4f57", "#eef0f6", "#a4a4ae", "#6c6c75", "#e2e4ee", "#4f4f57"],
    "ebony": ["#0f0c07", "#3a2f1e", "#1c1710", "#0b0906", "#2e2517", "#0f0c07"],
    "rose": ["#6d4436", "#f3d7c6", "#cfa088", "#9a6c56", "#eccdb8", "#6d4436"],
}


def grad(name: str, stops: list[str]) -> str:
    at = [0, 0.18, 0.42, 0.6, 0.8, 1]
    body = "".join(
        f'<stop offset="{o}" stop-color="{c}"/>' for o, c in zip(at, stops))
    return (f'<linearGradient id="{name}" x1="0" y1="0" x2="1" y2="1">'
            f"{body}</linearGradient>")


def dark_of(stops: list[str]) -> str:
    return stops[0]


# --------------------------------------------------------------- ornaments
def beading(fill: str, stroke: str, r: float = 8.4, step: float = 28.5) -> str:
    """A row of half-round beads down each rail — the commonest antique edge."""
    out = []
    x = BAND + 6
    while x < INNER - 6:
        for cx, cy in ((x, BAND - 4), (x, INNER + 4)):
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" '
                       f'fill="url(#{fill})" stroke="{stroke}" stroke-width="0.6"/>')
        for cy, cx in ((x, BAND - 4), (x, INNER + 4)):
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" '
                       f'fill="url(#{fill})" stroke="{stroke}" stroke-width="0.6"/>')
        x += step
    return "".join(out)


def egg_and_dart(fill: str, stroke: str) -> str:
    """Alternating egg and dart — the classical cyma recta enrichment."""
    out = []
    step = 34.4
    x = BAND + 18
    while x < INNER - 18:
        for along, across in ((x, 42.0), (x, SIZE - 42.0)):
            out.append(
                f'<ellipse cx="{along:.1f}" cy="{across:.1f}" rx="20.2" ry="25.2" '
                f'fill="url(#{fill})" stroke="{stroke}" stroke-width="1.3"/>'
                f'<ellipse cx="{along:.1f}" cy="{across:.1f}" rx="27.2" ry="31.2" '
                f'fill="none" stroke="url(#{fill}Dark)" stroke-width="2.4"/>')
            d = along + step / 2
            if d < INNER - 18:
                out.append(f'<path d="M {d:.1f},{across-20.2:.1f} '
                           f'L {d-3:.1f},{across+20.2:.1f} '
                           f'L {d+3:.1f},{across+20.2:.1f} Z" '
                           f'fill="url(#{fill}Dark)"/>')
        for across, along in ((x, 42.0), (x, SIZE - 42.0)):
            out.append(
                f'<ellipse cx="{along:.1f}" cy="{across:.1f}" rx="25.2" ry="20.2" '
                f'fill="url(#{fill})" stroke="{stroke}" stroke-width="1.3"/>'
                f'<ellipse cx="{along:.1f}" cy="{across:.1f}" rx="31.2" ry="27.2" '
                f'fill="none" stroke="url(#{fill}Dark)" stroke-width="2.4"/>')
            d = across + step / 2
            if d < INNER - 18:
                out.append(f'<path d="M {along-20.2:.1f},{d:.1f} '
                           f'L {along+20.2:.1f},{d-3:.1f} '
                           f'L {along+20.2:.1f},{d+3:.1f} Z" '
                           f'fill="url(#{fill}Dark)"/>')
        x += step * 2
    return "".join(out)


def fluting(fill: str, stroke: str) -> str:
    """Plain reeded molding — the quiet frames on the board."""
    out = []
    for off in (18, 30, 42, 54, 66):
        out.append(
            f'<rect x="{off}" y="{off}" width="{SIZE-2*off}" height="{SIZE-2*off}" '
            f'fill="none" stroke="url(#{fill}{"Dark" if off % 24 else ""})" '
            f'stroke-width="{3 if off % 24 else 5}" stroke-opacity="0.9"/>')
    return "".join(out)


def ropework(fill: str, stroke: str) -> str:
    """Twisted rope torus — common on Victorian ovals."""
    out = []
    step = 16.0
    for i in range(int((INNER - BAND) / step) + 1):
        p = BAND + i * step
        for cx, cy in ((p, 40), (p, SIZE - 40), (40, p), (SIZE - 40, p)):
            out.append(
                f'<path d="M {cx-8:.1f},{cy-8:.1f} Q {cx:.1f},{cy:.1f} '
                f'{cx+8:.1f},{cy-8:.1f} Q {cx:.1f},{cy+4:.1f} '
                f'{cx-8:.1f},{cy-8:.1f} Z" fill="url(#{fill})" '
                f'stroke="{stroke}" stroke-width="0.7"/>')
    return "".join(out)


def volute(fill: str, stroke: str, scale: float = 1.0) -> str:
    """Acanthus corner volute — a logarithmic spiral of leaf, mirrored into all
    four corners. This is the shape that reads as 'baroque' at a glance.

    Everything here must stay inside the BANDxBAND corner tile: border-image
    slices the corners at BAND, so ornament drawn beyond that is simply cut
    off — which is exactly what made the first pass look like plain beading."""
    C = BAND / 2                       # centre of the corner tile
    R = BAND / 2 - 4                   # keep the whole spiral inside the slice
    pts = []
    for i in range(28):
        t = i / 27
        a = t * 3.4 * math.pi
        r = 4 + R * math.exp(-1.15 * t) * scale
        pts.append((C + math.cos(a) * r * 0.95, C - math.sin(a) * r * 0.72))
    spiral = " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    leaf = "".join(
        f'<path d="M {C:.1f},{C:.1f} Q {C+0.55*R*math.cos(k):.1f},'
        f'{C-0.5*R*math.sin(k)-3*i:.1f} {C+R*math.cos(k):.1f},'
        f'{C-0.86*R*math.sin(k)-2*i:.1f} Q {C+0.5*R*math.cos(k):.1f},'
        f'{C-0.23*R*math.sin(k):.1f} {C:.1f},{C:.1f} Z" fill="url(#{fill})" '
        f'stroke="{stroke}" stroke-width="1.2"/>'
        for i, k in enumerate((0.35, 0.72, 1.15)))
    body = (f'{leaf}<path d="M {spiral}" fill="none" stroke="url(#{fill})" '
            f'stroke-width="{9*scale:.1f}" stroke-linecap="round"/>'
            f'<path d="M {spiral}" fill="none" stroke="{stroke}" stroke-width="1"/>')
    corners = []
    for rot, (ox, oy) in ((0, (0, 0)), (90, (SIZE - BAND, 0)),
                          (180, (SIZE - BAND, SIZE - BAND)),
                          (270, (0, SIZE - BAND))):
        corners.append(f'<g transform="translate({ox},{oy}) '
                       f'rotate({rot} {C} {C})">{body}</g>')
    return "".join(corners)


def shell_crest(fill: str, stroke: str) -> str:
    """A scallop shell at the top centre — the rococo cartouche."""
    rays = "".join(
        f'<path d="M 220,30 L {220 + 46*math.cos(a):.1f},'
        f'{30 + 42*math.sin(a):.1f}" stroke="{stroke}" stroke-width="1.1"/>'
        for a in [math.radians(d) for d in range(200, 341, 20)])
    return (f'<path d="M 174,44 Q 220,-16 266,44 Q 220,26 174,44 Z" '
            f'fill="url(#{fill})" stroke="{stroke}" stroke-width="1.4"/>{rays}'
            f'<path d="M 174,{SIZE-44} Q 220,{SIZE+16} 266,{SIZE-44} '
            f'Q 220,{SIZE-26} 174,{SIZE-44} Z" fill="url(#{fill})" '
            f'stroke="{stroke}" stroke-width="1.4"/>')


EDGES = {"beading": beading, "egg": egg_and_dart,
         "fluting": fluting, "rope": ropework}


# ---------------------------------------------------------------- profiles
# (css class, palette, edge treatment, corner ornament, shell crest?)
PROFILES = [
    ("f-baroque",  "gold",     "egg",     "volute", True),
    ("f-rococo",   "gold",     "beading", "volute", True),
    ("f-gilt",     "paleGold", "egg",     "volute", False),
    ("f-carved",   "gold",     "rope",    "volute", False),
    ("f-silver",   "silver",   "beading", "volute", False),
    ("f-ebony",    "ebony",    "fluting", None,     False),
    ("f-plain",    "paleGold", "fluting", None,     False),
    ("f-rose",     "rose",     "beading", None,     False),
]


def build_svg(palette: str, edge: str, corner: str | None, crest: bool) -> str:
    stops = PALETTES[palette]
    dark = dark_of(stops)
    defs = (grad(palette, stops)
            + grad(palette + "Dark", [stops[0], stops[3], stops[0],
                                      stops[0], stops[3], stops[0]])
            + f'<radialGradient id="bead" cx="0.35" cy="0.3" r="0.8">'
              f'<stop offset="0" stop-color="{stops[1]}"/>'
              f'<stop offset="0.6" stop-color="{stops[2]}"/>'
              f'<stop offset="1" stop-color="{stops[0]}"/></radialGradient>')
    # the molding itself: a square annulus, filled with the sheen ramp
    parts = [
        f'<path fill-rule="evenodd" fill="url(#{palette})" '
        f'd="M0,0 H{SIZE} V{SIZE} H0 Z M{BAND},{BAND} H{INNER} V{INNER} '
        f'H{BAND} Z"/>',
        f'<rect x="3" y="3" width="{SIZE-6}" height="{SIZE-6}" fill="none" '
        f'stroke="{stops[1]}" stroke-opacity="0.35" stroke-width="2"/>',
        f'<rect x="{BAND}" y="{BAND}" width="{INNER-BAND}" height="{INNER-BAND}" '
        f'fill="none" stroke="{stops[2]}" stroke-width="3"/>',
        f'<rect x="1" y="1" width="{SIZE-2}" height="{SIZE-2}" fill="none" '
        f'stroke="{dark}" stroke-width="2"/>',
    ]
    if edge == "beading":
        parts.append(beading("bead", dark))
    else:
        parts.append(EDGES[edge](palette, dark))
    if crest:
        parts.append(shell_crest(palette, dark))
    if corner:
        parts.append(volute(palette, dark))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {SIZE} {SIZE}"><defs>{defs}</defs>'
            + "".join(parts) + "</svg>")


def data_uri(svg: str) -> str:
    b64 = base64.b64encode(svg.encode()).decode()
    return f'url("data:image/svg+xml;base64,{b64}")'


def main() -> None:
    css = ["/* GENERATED by tools/make_frames.py — do not hand-edit.",
           "   Antique frame profiles for the gallery wall: a molding palette,",
           "   an edge enrichment, and a corner ornament, from a reference",
           "   board of real antique frames. */", ""]
    for cls, palette, edge, corner, crest in PROFILES:
        stops = PALETTES[palette]
        svg = build_svg(palette, edge, corner, crest)
        css.append(f".frame.{cls} {{")
        css.append("  background:")
        css.append("    repeating-linear-gradient(45deg, #0000001f 0 2px,"
                   " #ffffff12 2px 5px),")
        css.append(f"    linear-gradient(135deg, {stops[0]} 0%, {stops[1]} 20%,"
                   f" {stops[2]} 42%, {stops[3]} 58%, {stops[4]} 76%,"
                   f" {stops[5]} 100%);")
        css.append(f"  border-image: {data_uri(svg)} {BAND} round;")
        css.append("}")
        css.append("")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(css))
    kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT} ({kb:.0f} KB, {len(PROFILES)} frame styles)")


if __name__ == "__main__":
    main()
