# PyInstaller spec for the Constellation backend (memoryvault-brain).
# Build from the scripts/ directory:  pyinstaller constellation-backend.spec
# Produces dist/memoryvault-brain/  (onedir — reliable for the native ML libs).
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# heavy native/ML packages: collect their data files, dynamic libs, submodules
for pkg in ("insightface", "onnxruntime", "reverse_geocoder", "cv2",
            "pillow_heif", "imagehash", "PIL"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass  # a missing optional package just won't be bundled

# memoryvault's own lazily-imported submodules (cli.py does `from .X import Y`)
hiddenimports += collect_submodules("memoryvault")

# the web UI's static assets + the tag schema
# The web UI's static assets. The package was renamed brain -> constellation,
# and this path silently stopped matching: PyInstaller does not error on a
# datas entry that resolves to nothing, so the bundle would have shipped with
# no UI at all and only failed when someone opened it. Resolve it and assert.
_static = os.path.join("memoryvault", "constellation", "static")
if not os.path.isdir(_static):
    raise SystemExit(
        "constellation-backend.spec: %s not found — the web UI would ship "
        "empty. Has the package been renamed again?" % _static)
datas += [(_static, _static)]
schema = os.path.join("..", "schema", "tag-schema.json")
if os.path.exists(schema):
    datas += [(schema, "schema")]

a = Analysis(
    ["backend_entry.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name="memoryvault-brain", console=True,
          disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, name="memoryvault-brain")
