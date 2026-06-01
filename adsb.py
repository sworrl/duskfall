"""ADS-B aircraft tracking via adsb.lol regional queries with sub-classification.

Queries multiple geographic regions to cover major air corridors globally.
Rotates through 2 regions per cycle at 30s intervals for memory efficiency.
Full rotation through all regions takes ~5 minutes.
"""
import json
from datetime import datetime, timezone

from app.services.base_collector import BaseCollector

CATEGORY_MAP = {
    "A1": "light", "A2": "general_aviation", "A3": "airliner",
    "A4": "airliner", "A5": "heavy", "A6": "heavy", "A7": "helicopter",
    "B1": "light", "B2": "light", "B4": "light", "B6": "light", "B7": "light",
}

TYPE_OVERRIDES = {
    "B738": "airliner", "B739": "airliner", "A320": "airliner", "A321": "airliner",
    "A319": "airliner", "B737": "airliner", "B77W": "airliner", "B772": "airliner",
    "B788": "airliner", "B789": "airliner", "A332": "airliner", "A333": "airliner",
    "A359": "airliner", "A35K": "airliner", "B763": "airliner", "B764": "airliner",
    "E190": "airliner", "E195": "airliner", "CRJ9": "airliner", "CRJ7": "airliner",
    "A20N": "airliner", "A21N": "airliner", "B38M": "airliner", "B39M": "airliner",
    "A380": "heavy", "B744": "heavy", "B748": "heavy", "AN124": "heavy",
    "A346": "heavy", "B77L": "heavy", "C17": "heavy",
    "B74S": "cargo", "B77F": "cargo",
    "B429": "helicopter", "EC35": "helicopter", "EC45": "helicopter", "S76": "helicopter",
    "B407": "helicopter", "R44": "helicopter", "R22": "helicopter", "AS50": "helicopter",
    "H60": "helicopter", "UH60": "helicopter", "AH64": "helicopter",
    "C172": "general_aviation", "C182": "general_aviation", "C208": "general_aviation",
    "PA28": "general_aviation", "PA32": "general_aviation", "SR22": "general_aviation",
    "PC12": "general_aviation", "TBM9": "general_aviation",
    "GLF5": "bizjet", "GLF6": "bizjet", "GL5T": "bizjet", "C680": "bizjet",
    "CL60": "bizjet", "LJ45": "bizjet", "LJ60": "bizjet", "F2TH": "bizjet",
    "FA7X": "bizjet", "FA8X": "bizjet", "C56X": "bizjet", "C750": "bizjet",
    "GLEX": "bizjet", "E55P": "bizjet", "PC24": "bizjet", "GALX": "bizjet",
    "CL35": "bizjet", "C25B": "bizjet", "C525": "bizjet", "C510": "bizjet",
}

REGIONS = [
    (40.0, -74.0),    # NYC / Northeast
    (33.9, -84.4),    # Atlanta / Southeast
    (41.9, -87.6),    # Chicago / Midwest
    (29.8, -95.4),    # Houston / Gulf
    (33.4, -112.0),   # Phoenix / Southwest
    (37.8, -122.4),   # San Francisco / West Coast
    (47.6, -122.3),   # Seattle / Pacific NW
    (39.8, -104.9),   # Denver / Mountain
    (35.1, -106.6),   # Albuquerque / NM
    (25.8, -80.2),    # Miami / Florida
    (38.9, -77.0),    # DC / Mid-Atlantic
    (42.4, -71.0),    # Boston / New England
    (32.7, -117.2),   # San Diego / SoCal
    (36.1, -115.2),   # Las Vegas / Nevada
    (44.9, -93.3),    # Minneapolis / Upper Midwest
    (51.5, -0.1),     # London
    (48.9, 2.3),      # Paris
    (52.5, 13.4),     # Berlin
    (35.7, 139.7),    # Tokyo
]


def classify_aircraft(category: str, type_code: str) -> str:
    if type_code and type_code in TYPE_OVERRIDES:
        return TYPE_OVERRIDES[type_code]
    if category and category in CATEGORY_MAP:
        return CATEGORY_MAP[category]
    return "unknown"


class AdsbCollector(BaseCollector):
    feed_type = "adsb"
    interval_seconds = 30  # 30s between cycles, 2 regions per cycle

    _region_idx = 0

    async def fetch(self) -> list[dict]:
        # Query 2 regions per cycle — full rotation in ~5 minutes
        batch_size = 2
        start = AdsbCollector._region_idx
        regions_to_query = [REGIONS[i % len(REGIONS)] for i in range(start, start + batch_size)]
        AdsbCollector._region_idx = (start + batch_size) % len(REGIONS)

        seen_hex: set[str] = set()
        events: list[dict] = []

        for lat, lon in regions_to_query:
            try:
                resp = await self.client.get(
                    f"https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/250",
                    timeout=12,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                continue

            for ac in data.get("ac", []):
                ac_lat = ac.get("lat")
                ac_lon = ac.get("lon")
                if ac_lat is None or ac_lon is None:
                    continue

                hex_code = ac.get("hex", "unknown")
                if hex_code in seen_hex:
                    continue
                seen_hex.add(hex_code)

                callsign = (ac.get("flight") or "").strip()
                alt = ac.get("alt_baro", 0)
                if alt == "ground":
                    alt = 0

                category = ac.get("category", "")
                type_code = ac.get("t", "")
                sub_type = classify_aircraft(category, type_code)
                severity = "warning" if category in ("A5", "A6", "A7") else "info"

                desc_parts = []
                if callsign: desc_parts.append(callsign)
                if type_code: desc_parts.append(type_code)
                if sub_type != "unknown": desc_parts.append(f"[{sub_type}]")
                if ac.get("gs"): desc_parts.append(f"{ac['gs']:.0f}kts")
                if alt:
                    desc_parts.append(f"FL{int(alt)//100}" if int(alt) > 18000 else f"{alt}ft")

                raw = dict(ac)
                raw["_sub_type"] = sub_type
                raw["_source"] = "adsb.lol"

                events.append({
                    "uid": f"adsb-{hex_code}",
                    "title": callsign or hex_code,
                    "description": " | ".join(desc_parts),
                    "latitude": ac_lat,
                    "longitude": ac_lon,
                    "altitude": float(alt) if alt else 0,
                    "severity": severity,
                    "status": "active",
                    "source_url": f"https://globe.adsb.fi/?icao={hex_code}",
                    "raw_data": json.dumps(raw),
                    "last_updated": datetime.now(timezone.utc),
                    "first_seen": datetime.now(timezone.utc),
                })

        return events
