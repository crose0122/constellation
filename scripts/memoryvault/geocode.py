"""Offline reverse geocoding: GPS EXIF -> place tags (SPEC §5.2/§7).

reverse_geocoder resolves against a bundled geonames dataset — no network
call ever. Each located photo gets a `place` tag ("City, Region") which
becomes a searchable category/star like any other tag.
"""
from __future__ import annotations

from .db import record_error

US_STATES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
}


def geocode(conn) -> dict:
    import reverse_geocoder as rg

    rows = conn.execute(
        "SELECT id, gps_lat, gps_lon FROM photos WHERE gps_lat IS NOT NULL "
        "AND gps_lon IS NOT NULL AND id NOT IN "
        "(SELECT photo_id FROM tags WHERE dimension = 'place')").fetchall()
    if not rows:
        return {"geocoded": 0}
    coords = [(r["gps_lat"], r["gps_lon"]) for r in rows]
    results = rg.search(coords)  # batch K-D tree lookup, all offline
    stats = {"geocoded": 0, "errors": 0, "places": set()}
    for r, hit in zip(rows, results):
        try:
            city = hit.get("name", "")
            admin = hit.get("admin1", "")
            cc = hit.get("cc", "")
            if cc == "US" or admin in US_STATES:
                place = f"{city}, {admin}" if admin else city
            else:
                place = f"{city}, {cc}" if cc else city
            if not city:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO tags (photo_id, dimension, value, "
                "confidence, model_version) VALUES (?, 'place', ?, 0.9, "
                "'geocode-1.0')", (r["id"], place))
            stats["geocoded"] += 1
            stats["places"].add(place)
        except Exception as e:
            stats["errors"] += 1
            record_error(conn, "geocode", repr(e), photo_id=r["id"])
    conn.commit()
    stats["places"] = len(stats["places"])
    return stats
