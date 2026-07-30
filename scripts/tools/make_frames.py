#!/usr/bin/env python3
"""Generate the gallery wall's picture frames as CSS border-images.

These are rendered as real pixels, not as SVG shapes. Two earlier attempts drew
the frames as vector gradients — one with an SVG <filter> supplying the grain —
and both came out looking like moulded plastic: Android's WebView drops filter
effects when it rasterises a data-URI SVG for border-image, so the timber
arrived perfectly smooth. Painting the pixels here removes that dependency
entirely, and it's the only way to get grain that actually survives to the TV.

What makes it read as wood rather than a brown gradient:

  grain    fractal value noise, stretched ~40x along the length of each rail so
           it streaks like sawn timber, plus cathedral figure — the arcs you
           get when a saw cuts across the growth rings — from a warped sine.

  profile  a moulding is a carved cross-section, and what the eye reads as
           carving is light falling across it: outer arris, crown, the dark
           hollow of the cove, an inner fillet catching light, then the shadow
           of the rabbet. That's a ramp ACROSS the rail, and it does most of
           the work.

  mitre    a real frame is four lengths cut at 45 degrees, so the grain TURNS
           at each corner and a seam runs diagonally out of it. Grain that
           flows continuously around the perimeter looks wrong even when you
           can't say why.

Run after editing a timber:
    python3 tools/make_frames.py
"""
from __future__ import annotations

import base64
import io
import pathlib

import numpy as np
from PIL import Image

# Kept small deliberately: border-image slices the corners and repeats the
# rails, so a big canvas buys nothing but bytes — and these ship to a 2GB
# Android TV box.
SIZE = 256
BAND = 52
OUT = (pathlib.Path(__file__).resolve().parent.parent
       / "memoryvault" / "constellation" / "static" / "frames.css")


# ------------------------------------------------------------------- timber
# base, deep shadow, mid, crown highlight, grain strength, warm/cool figure
WOODS = {
    "walnut":     ("#4a352a", "#1a120c", "#5a4133", "#856048", 0.34, 0.55),
    "mahogany":   ("#553028", "#1d0f0b", "#663a30", "#8f5747", 0.32, 0.50),
    "oak":        ("#7a6244", "#33280f", "#8a7050", "#b39a72", 0.40, 0.78),
    "cherry":     ("#603a2c", "#24120c", "#734a38", "#9c6a52", 0.30, 0.48),
    "ebonised":   ("#211b16", "#070605", "#2c241d", "#463a2e", 0.42, 0.32),
    "driftwood":  ("#635b52", "#26221c", "#736a5f", "#9a9081", 0.44, 0.88),
    # gold leaf laid over a carved timber ground: keeps the grain and the
    # profile showing through, just a metal palette over it
    "giltwood":   ("#7e6528", "#2e230c", "#a0863c", "#dcc287", 0.20, 0.42),
    "silverleaf": ("#6b6d73", "#232529", "#878a91", "#c2c6ce", 0.22, 0.38),
}

# Light from above-left: the top rail catches it, the bottom rail is in
# shadow. This is what makes a flat ring read as a raised, physical frame.
RAIL_LIGHT = {"top": 1.16, "left": 1.02, "right": 0.86, "bottom": 0.70}

# The cross-section, as (position across the rail, brightness multiplier).
# Outer arris -> crown -> cove -> fillet -> rabbet shadow.
PROFILE = [
    (0.00, 0.55), (0.05, 1.18), (0.13, 1.02), (0.24, 0.62),
    (0.34, 0.74), (0.46, 1.06), (0.56, 0.88), (0.68, 0.66),
    (0.78, 1.10), (0.88, 0.80), (1.00, 0.42),
]


def hex_rgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float64)


def value_noise(shape, fx, fy, seed, octaves=4):
    """Fractal value noise. fx/fy are cells across the canvas — making one far
    larger than the other is what stretches the grain along the rail."""
    rng = np.random.default_rng(seed)
    h, w = shape
    total = np.zeros((h, w))
    amp, norm = 1.0, 0.0
    for o in range(octaves):
        gx = max(2, int(fx * (2 ** o)))
        gy = max(2, int(fy * (2 ** o)))
        grid = rng.random((gy + 1, gx + 1))
        # bilinear upsample with a smoothstep so cells don't show as diamonds
        yi = np.linspace(0, gy, h)
        xi = np.linspace(0, gx, w)
        y0, x0 = np.floor(yi).astype(int), np.floor(xi).astype(int)
        y1, x1 = np.minimum(y0 + 1, gy), np.minimum(x0 + 1, gx)
        ty, tx = yi - y0, xi - x0
        ty = (ty * ty * (3 - 2 * ty))[:, None]
        tx = (tx * tx * (3 - 2 * tx))[None, :]
        a = grid[np.ix_(y0, x0)]
        b = grid[np.ix_(y0, x1)]
        c = grid[np.ix_(y1, x0)]
        d = grid[np.ix_(y1, x1)]
        total += amp * ((a * (1 - tx) + b * tx) * (1 - ty)
                        + (c * (1 - tx) + d * tx) * ty)
        norm += amp
        amp *= 0.5
    return total / norm


def timber(shape, along_axis, seed, figure):
    """A slab of wood grain. along_axis=0 means the grain runs horizontally.

    Two layers: long stretched fibre, and cathedral arcs from a sine whose
    phase is warped by low-frequency noise — the arcs are what stops it
    reading as brushed metal."""
    h, w = shape
    # Frequencies must be scaled to the RAIL, not the canvas: a rail is only
    # BAND px across, so noise specified per-canvas lands as a handful of fat
    # blobs and the timber reads as watered silk. LINES is how many grain
    # lines cross the rail; convert that to cells across the whole canvas.
    # ~18 lines across a BAND-wide rail. Much finer than this and the fibre
    # lands at pixel pitch, which aliases into corduroy rather than timber.
    LINES = 18
    long_side = w if along_axis == 0 else h
    across = LINES * long_side / BAND
    if along_axis == 0:                       # grain runs left-right
        fibre = value_noise(shape, 2, across, seed, 2)
        streak = value_noise(shape, 9, across * 0.3, seed + 7, 2)
        pore = value_noise(shape, 40, across * 1.9, seed + 23, 1)
        wander = value_noise(shape, 7, 3, seed + 55, 2)
    else:                                     # grain runs top-bottom
        fibre = value_noise(shape, across, 2, seed, 2)
        streak = value_noise(shape, across * 0.3, 9, seed + 7, 2)
        pore = value_noise(shape, across * 1.9, 40, seed + 23, 1)
        wander = value_noise(shape, 3, 7, seed + 55, 2)

    # Real fibre is never ruler-straight: displace the grain along its own
    # width by a slow wander so the lines drift and occasionally converge,
    # which is most of what separates timber from brushed metal.
    yy, xx = np.mgrid[0:h, 0:w]
    # subtle: a big displacement smears the carved profile into waves and
    # the moulding stops reading as a solid, machined section
    shift = ((wander - 0.5) * BAND * 0.16).astype(int)
    if along_axis == 0:
        fibre = fibre[np.clip(yy + shift, 0, h - 1), xx]
        streak = streak[np.clip(yy + shift // 2, 0, h - 1), xx]
    else:
        fibre = fibre[yy, np.clip(xx + shift, 0, w - 1)]
        streak = streak[yy, np.clip(xx + shift // 2, 0, w - 1)]

    g = 0.58 * fibre + 0.26 * streak + 0.16 * pore
    g = (1 - figure) * (0.7 * fibre + 0.3 * streak) + figure * g
    g = (g - g.min()) / max(1e-6, g.max() - g.min())
    # gamma rather than linear contrast: darkens the late-wood lines without
    # blowing the light timber out to paper white
    return np.clip(g ** 1.25, 0, 1)


def build_frame(wood) -> Image.Image:
    base_h, dark_h, mid_h, crown_h, grain_amp, figure = wood
    base, dark, mid, crown = (hex_rgb(c) for c in (base_h, dark_h, mid_h, crown_h))
    N, B = SIZE, BAND
    y, x = np.mgrid[0:N, 0:N].astype(np.float64)

    # Which mitred rail owns each pixel — the 45-degree cuts are just the
    # diagonals of the square.
    top = (y <= x) & (y <= N - 1 - x)
    bottom = (y >= x) & (y >= N - 1 - x)
    left = (x < y) & (x <= N - 1 - y)
    right = (x > y) & (x >= N - 1 - y)

    # Distance across the rail, 0 at the outer edge, 1 at the rabbet.
    t = np.zeros((N, N))
    t[top] = (y / (B - 1))[top]
    t[bottom] = ((N - 1 - y) / (B - 1))[bottom]
    t[left] = (x / (B - 1))[left]
    t[right] = ((N - 1 - x) / (B - 1))[right]
    t = np.clip(t, 0, 1)

    # The carved cross-section: brightness as a function of t.
    ps = np.array([p for p, _ in PROFILE])
    pv = np.array([v for _, v in PROFILE])
    shade = np.interp(t, ps, pv)

    # Per-rail lighting.
    light = np.ones((N, N))
    light[top] = RAIL_LIGHT["top"]
    light[bottom] = RAIL_LIGHT["bottom"]
    light[left] = RAIL_LIGHT["left"]
    light[right] = RAIL_LIGHT["right"]

    # Base colour blends toward the crown where the profile is lit, toward the
    # deep shadow where it is not — so the timber changes hue, not just value.
    k = np.clip((shade - 0.42) / 0.9, 0, 1)[..., None]
    col = np.where(k > 0.5,
                   mid + (crown - mid) * (k - 0.5) * 2,
                   dark + (mid - dark) * k * 2)
    col = col * (0.55 + 0.65 * shade)[..., None] * light[..., None]

    # Grain: horizontal slab for the top/bottom rails, vertical for the sides,
    # so the figure turns at every mitre.
    gh = timber((N, N), 0, 11, figure)
    gv = timber((N, N), 1, 29, figure)
    g = np.where(top | bottom, gh, gv)
    # centred on 1.0 so grain_amp is literally the swing: 0.5 => 0.5x .. 1.5x
    col *= (1.0 + grain_amp * (g - 0.5) * 2.0)[..., None]

    # The mitre seams themselves: a thin darkening along each diagonal.
    d1 = np.abs(y - x)
    d2 = np.abs(y - (N - 1 - x))
    seam = np.minimum(d1, d2)
    col *= (0.72 + 0.28 * np.clip(seam / 2.2, 0, 1))[..., None]

    # Sharpen the two edges a real frame shows most: the outer arris and the
    # inner lip against the picture.
    edge = np.minimum.reduce([x, y, N - 1 - x, N - 1 - y])
    col *= (0.5 + 0.5 * np.clip(edge / 1.6, 0, 1))[..., None]
    inner = np.abs(t - 1.0) * (B - 1)
    col *= (0.55 + 0.45 * np.clip(inner / 2.0, 0, 1))[..., None]

    rgb = np.clip(col, 0, 255).astype(np.uint8)
    alpha = np.where((x < B) | (x >= N - B) | (y < B) | (y >= N - B), 255, 0)
    out = np.dstack([rgb, alpha.astype(np.uint8)])
    return Image.fromarray(out, "RGBA")


def data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f'url("data:image/png;base64,{b64}")'


def main() -> None:
    css = ["/* GENERATED by tools/make_frames.py — do not hand-edit.",
           "   Timber picture frames: procedural grain and cathedral figure,",
           "   a carved cross-section read as light across the moulding, and",
           "   45-degree mitres where the grain turns. Raster, because SVG",
           "   filters do not survive border-image rasterisation in WebView. */",
           ""]
    for name, wood in WOODS.items():
        img = build_frame(wood)
        base = wood[0]
        css.append(f".frame.f-{name} {{")
        css.append(f"  background: {base};")   # shows only in hairline gaps
        # stretch, not round: the grain does not tile, so `round` shows a
        # seam at every repeat. Stretching runs along the fibre, which is
        # simply what a longer length of the same moulding looks like.
        css.append(f"  border-image: {data_uri(img)} {BAND} stretch;")
        css.append("}")
        css.append("")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(css))
    print(f"wrote {OUT} ({OUT.stat().st_size/1024:.0f} KB, {len(WOODS)} frames)")


if __name__ == "__main__":
    main()
