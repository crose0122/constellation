"""M1.5 demo fixture — generates a synthetic library so the Brain renders
without touching a single real photo. Uses the REAL pipeline code paths
(discover → ingest → dedup → edges); only screening/tagging are stubbed,
since demo images need no NSFW model and no GPU."""

import random
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

from . import config, db
from .discover import discover
from .ingest import ingest
from .dedup import dedup
from .edges import compute_edges

PEOPLE = [
    ("Alex", (232, 182, 76)),
    ("Bailey", (90, 162, 224)),
    ("Dana", (94, 201, 143)),
    ("Casey", (143, 106, 224)),
    ("Elliot", (224, 110, 110)),
]
PLACES = ["Home", "Beach", "Park", "Grandmas House", "School"]
OCCASIONS = ["Birthday", "Holiday", "Vacation", "Everyday", "Graduation"]


def _demo_image(path: Path, hue: tuple, label: str, seed: int):
    rng = random.Random(seed)
    img = Image.new("RGB", (640, 480), tuple(int(c * 0.25) for c in hue))
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(6):
        x, y = rng.randint(0, 640), rng.randint(0, 480)
        r = rng.randint(40, 160)
        jitter = tuple(min(255, c + rng.randint(-40, 60)) for c in hue)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(*jitter, 90))
    d.ellipse([250, 150, 390, 290], fill=hue)
    d.text((305, 200), label[0], fill=(20, 20, 30))
    img.save(path, "JPEG", quality=88)


def build_demo(target: Path, n: int = 60) -> Path:
    """Create a self-contained demo library under `target`; returns db path."""
    target.mkdir(parents=True, exist_ok=True)
    config.LIBRARY_ROOT = target
    config.DB_PATH = target / "photos.db"

    src = Path(tempfile.mkdtemp(prefix="brain-demo-src-"))
    rng = random.Random(42)

    plan = []  # (filename, person, place, occasion, day_offset)
    today = date.today()
    for i in range(n):
        person, hue = PEOPLE[i % len(PEOPLE)]
        place = rng.choice(PLACES)
        occasion = rng.choice(OCCASIONS)
        # cluster shots on shared days; sprinkle "on this day" anniversaries
        if i % 7 == 0:
            day = date(today.year - rng.randint(1, 6), today.month, today.day)
        else:
            day = today - timedelta(days=rng.randint(30, 2000))
        fname = f"IMG_{1000 + i}.jpg"
        _demo_image(src / fname, hue, person, seed=i)
        plan.append((fname, person, place, occasion, day))

    # a few near-duplicates (resized copies) so dedup has something real to do
    for i in (3, 11):
        with Image.open(src / f"IMG_{1000 + i}.jpg") as img:
            img.resize((320, 240)).save(src / f"IMG_{1000 + i}_copy.jpg", "JPEG")

    conn = db.init(config.DB_PATH)
    discover(conn, src, kind="local", description="brain demo fixture")
    ingest(conn)
    dedup(conn)

    # stub screening + tagging: demo content is synthetic and safe by construction
    by_name = {p[0]: p for p in plan}
    for row in conn.execute("SELECT p.id, f.source_path FROM photos p "
                            "JOIN files f ON f.photo_id = p.id "
                            "AND f.disposition='canonical'").fetchall():
        fname = Path(row["source_path"]).name
        if fname not in by_name:
            continue
        _, person, place, occasion, day = by_name[fname]
        taken = datetime(day.year, day.month, day.day, 12, 0).isoformat()
        conn.execute(
            "UPDATE photos SET status='tagged', taken_at=?, screen_score=0.01 "
            "WHERE id=?",
            (taken, row["id"]),
        )
        mv = "demo@schema-1.0"
        for dim, value in (
            ("people", person), ("location", place),
            ("occasion", occasion), ("year", str(day.year)),
        ):
            conn.execute(
                "INSERT OR REPLACE INTO tags(photo_id, dimension, value, model_version) "
                "VALUES (?,?,?,?)",
                (row["id"], dim, value, mv),
            )
    conn.commit()

    stats = compute_edges(conn)
    print(f"demo library at {target}: {db.funnel(conn)['photos_ingested']} photos, "
          f"{stats['edges']} edges")
    conn.close()
    return config.DB_PATH
