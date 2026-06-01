"""Precache landing-preview map tiles.

Submits a precache job for each PREVIEW_CITY in LandingPreviewMap.tsx so
the dark CartoDB tiles are warm on the duskfall server's disk by the
time visitors land. Each POST is async — FastAPI runs the precache in a
background task and returns immediately.

Run on the falcon-vm against localhost (port 8500) to bypass CF/nginx.
"""
import json
import sys
import urllib.request
import urllib.parse

CITIES = [
    ("New York", 40.7128, -74.006),
    ("Los Angeles", 34.0522, -118.2437),
    ("Chicago", 41.8781, -87.6298),
    ("Houston", 29.7604, -95.3698),
    ("Miami", 25.7617, -80.1918),
    ("San Francisco", 37.7749, -122.4194),
    ("Seattle", 47.6062, -122.3321),
    ("Denver", 39.7392, -104.9903),
    ("Atlanta", 33.749, -84.388),
    ("Washington D.C.", 38.9072, -77.0369),
    ("Dallas", 32.7767, -96.797),
    ("Phoenix", 33.4484, -112.074),
    ("Boston", 42.3601, -71.0589),
    ("Las Vegas", 36.1699, -115.1398),
    ("Minneapolis", 44.9778, -93.265),
    ("Area 51", 37.2343, -115.8067),
    ("Cheyenne Mtn", 38.7444, -104.8467),
    ("White Sands", 32.9419, -106.4178),
    ("Creech AFB", 36.5861, -115.6733),
    ("Tonopah", 37.7946, -116.7651),
    ("London", 51.5074, -0.1278),
    ("Paris", 48.8566, 2.3522),
    ("Berlin", 52.52, 13.405),
    ("Tokyo", 35.6762, 139.6503),
    ("Sydney", -33.8688, 151.2093),
]

DELTA = 1.2  # ~135km box per side at mid latitudes
MIN_ZOOM = 5
MAX_ZOOM = 11
BASE = "http://127.0.0.1:8500"

for name, lat, lon in CITIES:
    qs = urllib.parse.urlencode({
        "source": "carto_dark",
        "south": lat - DELTA,
        "west": lon - DELTA,
        "north": lat + DELTA,
        "east": lon + DELTA,
        "min_zoom": MIN_ZOOM,
        "max_zoom": MAX_ZOOM,
    })
    url = f"{BASE}/api/tiles/precache?{qs}"
    try:
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read().decode())
            status = body.get("status", "?")
            print(f"  {name:22s} -> {status}")
    except Exception as e:
        print(f"  {name:22s} -> FAIL: {e}", file=sys.stderr)

print(f"\nQueued precache for {len(CITIES)} cities at zoom {MIN_ZOOM}-{MAX_ZOOM}.")
print("Background tasks run inside the FastAPI worker — poll /api/tiles/cache/stats.")
