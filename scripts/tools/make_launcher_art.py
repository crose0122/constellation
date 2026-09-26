#!/usr/bin/env python3
"""Generate every piece of Constellation's app identity from one star figure.

The launcher tile, the Android TV banner, the adaptive icon and the PWA icons
used to be three unrelated drawings: a candy-coloured hub-and-spoke graph on
the web, a blue stick-figure of stars in the Android vector, and a banner that
pasted the first one — black square and all — onto a navy gradient beside bold
Arial. Three marks, no family resemblance, and none of them looked like the
app they open.

So all of it is drawn here, from a single asterism defined once in FIGURE, and
every surface is a different framing of that one drawing.

What makes it read as sky rather than a node diagram:

  additive light   stars are composited by adding light, not by painting discs
                   over a background, so where two glows overlap they brighten
                   and blend hue the way real ones do. A flat dot cannot do
                   that, and flat dots are exactly what made the old mark look
                   like a graph database.

  white cores      a star is white at the centre and coloured in its halo. The
                   old icon coloured the whole dot, which is why it read as
                   plastic. Here the hue lives in the glow and the core burns
                   out to white, so the colour survives being shrunk to 48px
                   while the shape stays legible.

  the app's hues   the halo colours are the connection legend from the app
                   itself — gold for the same person, blue for the same place,
                   violet for visually similar, green for the same day. The
                   icon is a small picture of what the app actually does.

  an asterism      real constellations are lopsided: a bright anchor, a loop,
                   and a tail wandering off. The figure below is drawn that way
                   on purpose. Radial symmetry is what made the old mark look
                   like a network topology rather than a patch of sky.

Everything is deterministic — same seed, same pixels — so regenerating never
produces a spurious diff.

    python3 scripts/tools/make_launcher_art.py            # write all assets
    python3 scripts/tools/make_launcher_art.py --preview  # + build/ previews

Needs Pillow and numpy. The OFL-licensed Lato wordmark fonts are pinned under
``scripts/tools/assets/fonts/lato`` so output never depends on host fonts.
"""
from __future__ import annotations

import argparse
import functools
import io
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------- palette ---
# Straight from the app: constellation.css's sky, and the #legend swatches
# that label why two photos are connected.
VOID = (0x01, 0x04, 0x09)
GOLD = (0xE8, 0xB6, 0x4C)   # same person
BLUE = (0x5A, 0xA2, 0xE0)   # same place
VIOLET = (0x8F, 0x6A, 0xE0)  # visually similar
GREEN = (0x5E, 0xC9, 0x8F)  # same day
SLATE = (0x8A, 0x8A, 0x99)  # near in time
ICE = (0xCF, 0xE4, 0xFF)    # unlabelled starlight
LINE = (0x4D, 0x8F, 0xD6)   # the joining lines

# Sky gradient, sampled from the centre of the figure outwards.
SKY = [
    (0.00, (0x13, 0x27, 0x42)),
    (0.30, (0x0A, 0x19, 0x2E)),
    (0.62, (0x05, 0x0D, 0x1A)),
    (1.00, VOID),
]

# ----------------------------------------------------------------- figure ---
# Unit coordinates, y down. A bright anchor (the gold "same person" star), a
# five-star loop around it, and a two-star tail off to the upper left.
FIGURE = {
    "g": (0.055, 0.395, 0.30, SLATE),
    "b": (0.240, 0.185, 0.62, BLUE),
    "a": (0.460, 0.470, 1.00, GOLD),
    "d": (0.700, 0.120, 0.72, ICE),
    "e": (0.940, 0.440, 0.52, VIOLET),
    "f": (0.660, 0.790, 0.46, GREEN),
    "c": (0.320, 0.835, 0.38, ICE),
}
EDGES = [("g", "b"), ("b", "a"), ("a", "d"), ("d", "e"),
         ("e", "f"), ("f", "c"), ("c", "a")]

FIG_W = 0.940 - 0.055   # 0.885
FIG_H = 0.835 - 0.120   # 0.715

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RES = os.path.join(REPO, "android", "app", "src", "main", "res")
STATIC = os.path.join(REPO, "scripts", "memoryvault", "constellation", "static")
INSTALLER = os.path.join(REPO, "installer")

LATO = os.path.join(os.path.dirname(__file__), "assets", "fonts", "lato",
                    "Lato-{}.ttf")


# ------------------------------------------------------------- primitives ---
def _grid(w: int, h: int):
    return np.meshgrid(np.arange(w, dtype=np.float64) + 0.5,
                       np.arange(h, dtype=np.float64) + 0.5)


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _sky(w: int, h: int, cx: float, cy: float) -> np.ndarray:
    """Radial gradient falling away from the figure, like the app's own body
    background (`radial-gradient(ellipse at 50% 40%, #061020, #010409)`)."""
    x, y = _grid(w, h)
    far = max(np.hypot(cx, cy), np.hypot(w - cx, cy),
              np.hypot(cx, h - cy), np.hypot(w - cx, h - cy))
    t = np.clip(np.hypot(x - cx, y - cy) / far, 0.0, 1.0)
    out = np.zeros((h, w, 3))
    for (t0, c0), (t1, c1) in zip(SKY, SKY[1:]):
        seg = (t >= t0) & (t <= t1)
        k = ((t - t0) / (t1 - t0))[seg][:, None]
        out[seg] = np.array(c0) * (1 - k) + np.array(c1) * k
    return out / 255.0


def _dust(rgb: np.ndarray, *, scale: float, seed: int, density: float) -> None:
    """Faint background stars. Additive, sub-pixel, and deliberately dim: they
    should be felt as texture, not counted."""
    h, w = rgb.shape[:2]
    rng = np.random.default_rng(seed)
    n = int(density * (w * h) / 4096.0)
    x, y = _grid(w, h)
    for _ in range(n):
        px, py = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(0.35, 0.95) * max(scale / 260.0, 0.55)
        mag = rng.uniform(0.10, 0.45)
        tint = np.array(ICE) / 255.0 * (0.75 + 0.25 * rng.random())
        d = np.hypot(x - px, y - py)
        rgb += (1.0 - _smoothstep(r * 0.5, r * 2.2, d))[..., None] * tint * mag


def _segment(rgb: np.ndarray, p0, p1, *, width: float, mag: float) -> None:
    h, w = rgb.shape[:2]
    x, y = _grid(w, h)
    ax, ay = p0
    bx, by = p1
    vx, vy = bx - ax, by - ay
    length2 = max(vx * vx + vy * vy, 1e-9)
    t = np.clip(((x - ax) * vx + (y - ay) * vy) / length2, 0.0, 1.0)
    d = np.hypot(x - (ax + t * vx), y - (ay + t * vy))
    colour = np.array(LINE) / 255.0
    core = 1.0 - _smoothstep(width * 0.5, width * 0.5 + 1.1, d)
    halo = (1.0 - _smoothstep(width * 0.5, width * 3.4, d)) * 0.28
    rgb += (core + halo)[..., None] * colour * mag


def _star(rgb: np.ndarray, p, *, mag: float, colour, scale: float,
          spike: bool = False) -> None:
    """One star: a wide coloured halo, a tighter bright bloom, and a white core.

    The halo carries the hue, the core burns out to white. That is what keeps
    the colour readable at 48px without the shape turning into a coloured blob.
    """
    h, w = rgb.shape[:2]
    x, y = _grid(w, h)
    px, py = p
    d = np.hypot(x - px, y - py)
    c = np.array(colour) / 255.0

    halo_r = max(scale * 0.150 * (0.45 + 0.55 * mag), 3.0)
    rgb += (np.clip(1.0 - d / halo_r, 0, 1) ** 2.6)[..., None] * c * (0.90 * mag)

    bloom_r = max(scale * 0.052 * (0.5 + 0.5 * mag), 1.6)
    bloom = (np.clip(1.0 - d / bloom_r, 0, 1) ** 2.0)[..., None]
    rgb += bloom * (c * 0.62 + 0.38) * (1.15 * mag)

    core_r = max(scale * 0.0175 * (0.55 + 0.45 * mag), 0.85)
    rgb += (1.0 - _smoothstep(core_r, core_r + 1.0, d))[..., None] * \
        (c * 0.18 + 0.82) * (1.05 * mag)

    if spike:
        # A restrained four-point flare on the anchor only — enough to say
        # "star", not enough to say "lens filter".
        reach = scale * 0.26
        for dx, dy in ((1.0, 0.0), (0.0, 1.0)):
            along = np.abs((x - px) * dx + (y - py) * dy)
            across = np.abs((x - px) * dy + (y - py) * dx)
            f = np.clip(1.0 - along / reach, 0, 1) ** 2.2
            f *= 1.0 - _smoothstep(0.4, max(scale * 0.011, 1.0), across)
            rgb += f[..., None] * (c * 0.35 + 0.65) * (0.30 * mag)


def render_sky(w: int, h: int, *, cx: float, cy: float, scale: float,
               seed: int = 7, dust: float = 1.0, line_mag: float = 0.55,
               star_mag: float = 1.0) -> np.ndarray:
    """Draw the figure centred on (cx, cy) with `scale` = its width in pixels."""
    fx = scale / FIG_W
    fy = fx  # never stretch the figure
    ox = cx - (0.055 + FIG_W / 2) * fx
    oy = cy - (0.120 + FIG_H / 2) * fy
    pt = {k: (ox + v[0] * fx, oy + v[1] * fy) for k, v in FIGURE.items()}

    rgb = _sky(w, h, cx, cy)
    if dust:
        _dust(rgb, scale=scale, seed=seed, density=dust)
    for u, v in EDGES:
        _segment(rgb, pt[u], pt[v], width=max(scale * 0.0062, 0.9), mag=line_mag)
    for key, (_, _, mag, colour) in FIGURE.items():
        _star(rgb, pt[key], mag=mag, colour=colour, scale=scale,
              spike=(key == "a" and scale > 90), )
    return rgb


def to_image(rgb: np.ndarray) -> Image.Image:
    return Image.fromarray((np.clip(rgb, 0, 1) * 255.0 + 0.5).astype(np.uint8), "RGB")


# ------------------------------------------------------------------ icons ---
def icon(size: int, *, safe: float, round_mask: bool = False) -> Image.Image:
    """Square app icon. `safe` is the fraction of the edge the figure may use."""
    rgb = render_sky(size, size, cx=size / 2, cy=size / 2,
                     scale=size * safe, dust=0.9 if size >= 96 else 0.0,
                     line_mag=0.44 if size >= 96 else 0.58)
    img = to_image(rgb).convert("RGBA")
    if round_mask:
        m = Image.new("L", (size * 4, size * 4), 0)
        ImageDraw.Draw(m).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
        img.putalpha(m.resize((size, size), Image.LANCZOS))
    return img


# ----------------------------------------------------------------- banner ---
def _tracked(draw: ImageDraw.ImageDraw, cx: float, baseline: float, text: str,
             font: ImageFont.FreeTypeFont, fill, tracking: float) -> None:
    widths = [font.getlength(ch) for ch in text]
    x = cx - (sum(widths) + tracking * (len(text) - 1)) / 2.0
    for ch, adv in zip(text, widths):
        draw.text((x, baseline), ch, font=font, fill=fill, anchor="ls")
        x += adv + tracking


def _font(style: str, size: int) -> ImageFont.FreeTypeFont:
    path = LATO.format(style)
    if not os.path.exists(path):
        sys.exit(f"missing repository font {path}")
    return ImageFont.truetype(path, size)


def banner(w: int = 320, h: int = 180) -> Image.Image:
    """The Android TV / Google TV launcher tile.

    Its neighbours on that row (Netflix, Prime Video, PBS) are all centred
    wordmarks on a quiet ground, so this is one too: mark above name, generous
    margins, nothing inside the 5% the TV may overscan away.
    """
    rgb = render_sky(w, h, cx=w / 2, cy=h * 0.325, scale=w * 0.285,
                     dust=1.35, line_mag=0.46)
    img = to_image(rgb).convert("RGBA")

    text = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(text)
    _tracked(draw, w / 2, h * 0.725, "CONSTELLATION",
             _font("Light", 25), (0xEE, 0xF5, 0xFF, 255), 2.9)
    _tracked(draw, w / 2, h * 0.855, "OUR LIFE IN IMAGES",
             _font("Semibold", 9), (0x7F, 0xA6, 0xC8, 235), 3.0)

    # A breath of glow under the type, so it sits in the sky instead of on it.
    glow = text.filter(ImageFilter.GaussianBlur(4.5))
    glow.putalpha(glow.getchannel("A").point(lambda v: int(v * 0.55)))
    img.alpha_composite(glow)
    img.alpha_composite(text)
    return img.convert("RGB")


# ------------------------------------------------------------ vector icon ---
def _circle_path(cx: float, cy: float, r: float) -> str:
    return (f"M{cx:.2f},{cy:.2f} m-{r:.2f},0 "
            f"a{r:.2f},{r:.2f} 0 1,0 {2 * r:.2f},0 "
            f"a{r:.2f},{r:.2f} 0 1,0 -{2 * r:.2f},0 Z")


def _argb(colour, alpha: float) -> str:
    return "#%02X%02X%02X%02X" % (int(round(np.clip(alpha, 0, 1) * 255)), *colour)


def _vector_figure(viewport: float, safe: float, *, mono: bool) -> str:
    """The same asterism as Android vector paths.

    A vector cannot add light, so each star is one disc filled with a radial
    gradient — white core, coloured halo, fading to nothing — which is the
    closest a drawable gets to the raster bloom. Stacked flat circles were the
    obvious alternative and they showed their own edges as rings.
    """
    scale = viewport * safe / FIG_W
    ox = viewport / 2 - (0.055 + FIG_W / 2) * scale
    oy = viewport / 2 - (0.120 + FIG_H / 2) * scale
    pt = {k: (ox + v[0] * scale, oy + v[1] * scale) for k, v in FIGURE.items()}
    out = []

    for u, v in EDGES:
        (x0, y0), (x1, y1) = pt[u], pt[v]
        out.append(
            f'    <path android:strokeColor="{"#FFFFFF" if mono else "#%02X%02X%02X" % LINE}" '
            f'android:strokeWidth="{viewport * 0.0105:.2f}" '
            f'android:strokeAlpha="{0.85 if mono else 0.62:.2f}" '
            f'android:strokeLineCap="round" '
            f'android:pathData="M{x0:.2f},{y0:.2f} L{x1:.2f},{y1:.2f}" />')

    for key, (_, _, mag, colour) in FIGURE.items():
        cx, cy = pt[key]
        r = viewport * 0.126 * (0.45 + 0.55 * mag)
        w = 0.55 + 0.45 * mag
        halo = (255, 255, 255) if mono else colour
        stops = [(0.00, (255, 255, 255), 1.00 * w),
                 (0.14, (255, 255, 255) if mono else ICE, 0.98 * w),
                 (0.27, halo, 0.80 * w),
                 (0.55, halo, 0.20 * w),
                 (1.00, halo, 0.00)]
        items = "\n".join(
            f'                <item android:offset="{o:.2f}" '
            f'android:color="{_argb(c, a)}" />' for o, c, a in stops)
        out.append(
            f'    <path android:pathData="{_circle_path(cx, cy, r)}">\n'
            f'        <aapt:attr name="android:fillColor">\n'
            f'            <gradient android:type="radial" '
            f'android:centerX="{cx:.2f}" android:centerY="{cy:.2f}" '
            f'android:gradientRadius="{r:.2f}">\n{items}\n'
            f'            </gradient>\n'
            f'        </aapt:attr>\n'
            f'    </path>')
    return "\n".join(out)


def vector_foreground(mono: bool = False) -> str:
    what = ("A flat white cut of the figure for Android 13 themed icons."
            if mono else
            "The same asterism as the raster icons, sized so even the stars' "
            "bloom stays inside the 72dp circle every adaptive mask shows.")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!-- Generated by scripts/tools/make_launcher_art.py — do not hand-edit.
     {what} -->
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:aapt="http://schemas.android.com/aapt"
    android:width="108dp"
    android:height="108dp"
    android:viewportWidth="108"
    android:viewportHeight="108">

{_vector_figure(108.0, 0.560, mono=mono)}
</vector>
"""


def vector_background() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<!-- Generated by scripts/tools/make_launcher_art.py — do not hand-edit.
     The app's own sky: a radial fall from #132742 to #010409. -->
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:aapt="http://schemas.android.com/aapt"
    android:width="108dp"
    android:height="108dp"
    android:viewportWidth="108"
    android:viewportHeight="108">
    <path android:pathData="M0,0h108v108h-108z">
        <aapt:attr name="android:fillColor">
            <gradient
                android:type="radial"
                android:centerX="54"
                android:centerY="54"
                android:gradientRadius="76">
                <item android:offset="0.0" android:color="#FF132742" />
                <item android:offset="0.30" android:color="#FF0A192E" />
                <item android:offset="0.62" android:color="#FF050D1A" />
                <item android:offset="1.0" android:color="#FF010409" />
            </gradient>
        </aapt:attr>
    </path>
</vector>
"""


ADAPTIVE = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@drawable/ic_launcher_background" />
    <foreground android:drawable="@drawable/ic_launcher_foreground" />
    <monochrome android:drawable="@drawable/ic_launcher_monochrome" />
</adaptive-icon>
"""


# ------------------------------------------------------------------ write ---
MIPMAPS = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}

# macOS icon members, each holding a PNG of that pixel size. ic11–ic14 are the
# @2x variants of 16, 32, 128 and 256.
ICNS_MEMBERS = (("ic07", 128), ("ic08", 256), ("ic09", 512), ("ic10", 1024),
                ("ic11", 32), ("ic12", 64), ("ic13", 256), ("ic14", 512))


def write_icns(path: str, member) -> None:
    """Write a .icns by hand.

    Pillow's own ICNS writer emits a `TOC ` member, which is legal and which
    electron-builder's icon converter then tries to decode as an image — it
    shells out to openjpeg, fails, and takes the whole installer build with it.
    Nothing needs the table of contents, so this writes the image members only.
    """
    import struct
    body = b""
    for kind, px in ICNS_MEMBERS:
        buf = io.BytesIO()
        member(px).save(buf, "PNG", optimize=True)
        png = buf.getvalue()
        body += kind.encode("ascii") + struct.pack(">I", len(png) + 8) + png
    with open(path, "wb") as fh:
        fh.write(b"icns" + struct.pack(">I", len(body) + 8) + body)
    print(f"  {os.path.relpath(path, REPO)}")


def write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(data, Image.Image):
        data.save(path, "PNG", optimize=True)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data)
    print(f"  {os.path.relpath(path, REPO)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preview", action="store_true",
                    help="also write oversized previews to build/launcher-art/")
    args = ap.parse_args()

    print("android launcher icons")
    for density, px in MIPMAPS.items():
        write(os.path.join(RES, f"mipmap-{density}", "ic_launcher.png"),
              icon(px, safe=0.66).convert("RGB"))
        write(os.path.join(RES, f"mipmap-{density}", "ic_launcher_round.png"),
              icon(px, safe=0.62, round_mask=True))

    print("android adaptive icon")
    write(os.path.join(RES, "drawable", "ic_launcher_foreground.xml"),
          vector_foreground())
    write(os.path.join(RES, "drawable", "ic_launcher_monochrome.xml"),
          vector_foreground(mono=True))
    write(os.path.join(RES, "drawable", "ic_launcher_background.xml"),
          vector_background())
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        write(os.path.join(RES, "mipmap-anydpi-v26", name), ADAPTIVE)

    print("android tv banner")
    write(os.path.join(RES, "drawable-xhdpi", "tv_banner.png"), banner())

    print("pwa icons")
    # "any maskable" in the manifest: Android crops to a circle inscribed in
    # the middle 80%, so the figure stays well inside that.
    write(os.path.join(STATIC, "icon-192.png"), icon(192, safe=0.56).convert("RGB"))
    write(os.path.join(STATIC, "icon-512.png"), icon(512, safe=0.56).convert("RGB"))

    print("desktop installer")
    # electron-builder reads these out of buildResources (installer/assets):
    # icon.png for Linux, icon.ico for Windows, icon.icns for macOS. They were
    # named in installer/package.json but had never been drawn, so every
    # installer build shipped the stock Electron atom.
    cut = functools.lru_cache(maxsize=None)(
        lambda px: icon(px, safe=0.66).convert("RGB"))
    write(os.path.join(INSTALLER, "assets", "icon.png"), cut(512))
    # Every .ico and .icns member is drawn at its own size rather than
    # downsampled from one master: shrinking 1024px of sky to 16px turns the
    # figure into mud.
    ico_sizes = (16, 24, 32, 48, 64, 128, 256)
    ico = os.path.join(INSTALLER, "assets", "icon.ico")
    os.makedirs(os.path.dirname(ico), exist_ok=True)
    cut(ico_sizes[-1]).save(ico, "ICO", sizes=[(s, s) for s in ico_sizes],
                            append_images=[cut(s) for s in ico_sizes[:-1]])
    print(f"  {os.path.relpath(ico, REPO)}")
    write_icns(os.path.join(INSTALLER, "assets", "icon.icns"), cut)

    if args.preview:
        print("previews")
        out = os.path.join(REPO, "build", "launcher-art")
        write(os.path.join(out, "banner-3x.png"),
              banner().resize((960, 540), Image.LANCZOS))
        write(os.path.join(out, "icon-512.png"), icon(512, safe=0.66).convert("RGB"))
        write(os.path.join(out, "icon-round-512.png"), icon(512, safe=0.62, round_mask=True))
        sheet = Image.new("RGB", (560, 120), VOID)
        x = 16
        for px in (48, 72, 96, 144, 192):
            sheet.paste(icon(px, safe=0.66).convert("RGB"), (x, (120 - px) // 2))
            x += px + 16
        write(os.path.join(out, "mipmap-contact-sheet.png"), sheet)


if __name__ == "__main__":
    main()
