"""CP3 E2E against the REAL bundled backend binary: a mixed phone dump —
iPhone HEICs (with GPS/date), a 6-frame burst, a RAW, an oversized file,
ordinary JPEGs — through the installer's first-sweep chain + screening, then
the running server: what's on the wall, what's parked, what's archived."""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pillow_heif
from PIL import Image, ImageDraw, ImageFilter

pillow_heif.register_heif_opener()
B = sys.argv[1]
MODEL = sys.argv[2]
root = Path(tempfile.mkdtemp(prefix="cp3e2e-", dir=sys.argv[3]))
src = root / "Pictures"; src.mkdir()
lib = root / "library"
env = {k: v for k, v in os.environ.items() if not k.startswith("MEMORYVAULT_")}
env.update(MEMORYVAULT_LIBRARY_ROOT=str(lib), MEMORYVAULT_VAULT_MODE="dir",
           MEMORYVAULT_NSFW_ONNX_PATH=MODEL, MEMORYVAULT_MAX_FILE_BYTES=str(5_000_000))
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + (f"  {x}" if x else ""), flush=True)
def run(*a):
    r = subprocess.run([B, *a], env=env, capture_output=True, text=True, timeout=600)
    if r.returncode: print(r.stdout[-400:], r.stderr[-800:])
    return r

def scene(seed):
    rng = np.random.default_rng(seed); im = Image.new("RGB", (1200, 900), (90, 140, 200)); d = ImageDraw.Draw(im)
    for _ in range(50):
        x, y = rng.integers(0, 1200), rng.integers(0, 900)
        d.rectangle([x, y, x + rng.integers(20, 200), y + rng.integers(20, 200)], fill=tuple(int(v) for v in rng.integers(0, 255, 3)))
    return im
def exif(dt, model="iPhone 15"):
    ex = Image.Exif(); ex[306] = dt; ex[272] = model; ex.get_ifd(0x8769)[36867] = dt
    g = ex.get_ifd(0x8825); g[1], g[2], g[3], g[4] = "N", (40.0, 26.0, 46.0), "W", (79.0, 58.0, 56.0)
    return ex.tobytes()

t0 = datetime(2025, 6, 1, 10, 0, 0)
for i in range(8):
    scene(100 + i).save(src / f"IMG_{i:04d}.HEIC", exif=exif((t0 + timedelta(hours=i)).strftime("%Y:%m:%d %H:%M:%S")))
base = scene(7)
for i in range(6):
    f = base.transform(base.size, Image.AFFINE, (1, 0, i * 2, 0, 1, i), fillcolor=(90, 140, 200))
    if i != 3: f = f.filter(ImageFilter.GaussianBlur(1.5 + 0.7 * i))
    f.save(src / f"BURST_{i}.jpg", quality=92, exif=exif((t0 + timedelta(days=2, milliseconds=250 * i)).strftime("%Y:%m:%d %H:%M:%S")))
for i in range(5):
    scene(300 + i).save(src / f"DSC_{i}.jpg", quality=90, exif=exif((t0 + timedelta(days=5 + i)).strftime("%Y:%m:%d %H:%M:%S"), "Pixel 8"))
(src / "DSC_9001.CR3").write_bytes(b"\x00\x00\x00\x18ftypcrx " + os.urandom(20000))
(src / "HUGE_clip.mov").write_bytes(os.urandom(6_000_000))
heic_hash = __import__("hashlib").sha256((src / "IMG_0000.HEIC").read_bytes()).hexdigest()

for step in (["init"], ["discover", str(src), "--kind", "local"],
             ["ingest", "--recent-first", "--progress-json", "--limit", "2000"],
             ["curate"], ["bursts"], ["screen"]):
    r = run(*step)
    check("stage " + step[0], r.returncode == 0)

import sqlite3
c = sqlite3.connect(lib / "photos.db"); c.row_factory = sqlite3.Row
q = lambda s, *a: c.execute(s, a).fetchall()
heics = q("SELECT p.* FROM photos p JOIN files f ON f.photo_id=p.id WHERE f.source_path LIKE '%.HEIC'")
check("8 HEICs ingested, working file is JPEG", len(heics) == 8 and all(h["library_path"].endswith(".jpg") for h in heics))
check("HEIC originals kept byte-for-byte", any(__import__("hashlib").sha256((lib / h["original_path"]).read_bytes()).hexdigest() == heic_hash for h in heics))
check("HEIC date+GPS carried", all(h["taken_at"] and h["gps_lat"] for h in heics))
parked = q("SELECT COUNT(*) n FROM photos WHERE status='parked'")[0]["n"]
keeper = q("SELECT p.status FROM photos p JOIN files f ON f.photo_id=p.id WHERE f.source_path LIKE '%BURST_3.jpg'")[0]["status"]
check("burst: 5 parked, sharpest (frame 3) kept", parked == 5 and keeper == "screened", f"parked={parked} keeper={keeper}")
raw = q("SELECT status, original_path FROM photos WHERE media_kind='raw'")
check("RAW archived, not in stream", len(raw) == 1 and raw[0]["status"] == "archived" and (lib / raw[0]["original_path"]).exists())
big = q("SELECT disposition FROM files WHERE source_path LIKE '%HUGE_clip.mov'")[0]["disposition"]
err = q("SELECT error FROM errors WHERE source_path LIKE '%HUGE_clip.mov'")
check("oversized file skipped with a plain reason", big == "too-large" and "skips files over" in err[0]["error"], err[0]["error"][:80] if err else "")
scr = q("SELECT COUNT(*) n FROM photos WHERE status='screened'")[0]["n"]
check("screened = 8 HEIC + 1 burst keeper + 5 JPEG", scr == 14, f"screened={scr}")

port = 29484
srv = subprocess.Popen([B, "constellation", "--host", "127.0.0.1", "--port", str(port), "--tls-port", "0"], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try: urllib.request.urlopen(f"http://127.0.0.1:{port}/wall", timeout=1); break
        except Exception: time.sleep(0.25)
    g = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/gallery?limit=200", timeout=10).read())
    ids = {p["id"] for p in g["photos"]}
    check("wall shows 14 photos (no parked, no RAW)", len(ids) == 14, f"wall={len(ids)}")
    h = heics[0]
    img = urllib.request.urlopen(f"http://127.0.0.1:{port}/display/{h['sha256'][:16]}.jpg", timeout=20).read()
    check("HEIC displays as JPEG in the browser", img[:3] == b"\xff\xd8\xff")
finally:
    srv.terminate(); srv.wait(timeout=10)
shutil.rmtree(root)
print(f"{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
