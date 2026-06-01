# Duskfall

OSINT situational awareness and escape routing platform.

Real-time mapping of threats, infrastructure, and intelligence feeds with camera-aware egress route planning. Built on the WanderMage mapping stack, integrated with OpenTAKServer for TAK ecosystem interop.

## Install

```bash
git clone https://github.com/sworrl/duskfall /tmp/duskfall-install && bash /tmp/duskfall-install/duskfall.sh --install && rm -rf /tmp/duskfall-install
```

Update:
```bash
~/.duskfall/duskfall.sh --update
```

Uninstall:
```bash
~/.duskfall/duskfall.sh --uninstall
```

## What It Does

- **Live OSINT Layers** — Earthquakes, wildfires, weather alerts, ADS-B aircraft, APRS ham radio, AIS maritime, S2 GhostNet stations, Waffle House Index, ISS tracking
- **Surveillance Awareness** — 73,000+ ALPR/traffic cameras mapped with route avoidance
- **Escape Route Planning** — Multi-egress route calculation with threat avoidance, camera-aware routing, bridge height/weight filtering, real-time hazard integration
- **Bug-Out Advisor** — One-tap auto-suggestion of the 5 safest exit destinations from your detected location, scored by live threat density and surveillance camera exposure
- **TAK Integration** — Bidirectional bridge with OpenTAKServer for CoT data sharing with ATAK/WinTAK/iTAK clients
- **Infrastructure Monitoring** — Waffle House Index (FEMA disaster metric), power grid awareness, road hazards
- **ADS-B Tracking** — Persistent military aircraft tracking with full historical plots per registration
- **IoT Sensors** — Arduino/ESP32 sensor nodes for environmental monitoring (radiation, air quality, motion, temperature)
- **Local LLM Intelligence** — Optional Ollama integration for event summarization, pattern detection, and natural language queries
- **Federation** — Mesh data sharing between Duskfall instances
- **Encrypted Relay** — Feeder-relay node with SHA-512 auth, E2E encrypted chat, video, and TAK forwarding

## Architecture

```
React 18 + Leaflet ──► FastAPI + PostGIS ──► OpenTAKServer
     (map UI)           (data engine)         (TAK bridge)
                              │
                              ├──► Ollama (local LLM)
                              ├──► Feeder-Relay (encrypted mesh)
                              └──► ESP32 Sensors (IoT)
```

### Stack
- **Frontend:** React 18, TypeScript, Leaflet, Vite
- **Backend:** FastAPI, SQLAlchemy, GeoAlchemy2 (PostGIS)
- **Database:** PostgreSQL + PostGIS
- **Routing:** OSRM / OpenRouteService
- **LLM:** Ollama (hardware-aware model selection)
- **TAK:** OpenTAKServer (CoT over TCP/WebSocket)
- **Relay:** AES-256-GCM encrypted, SHA-512 API keys

### Data Sources (all free, no auth required)
| Feed | Source | Update |
|------|--------|--------|
| Earthquakes | USGS GeoJSON | 5 min |
| Weather Alerts | NWS CAP | 2 min |
| Active Fires | NASA FIRMS / NIFC | 30 min |
| Aircraft | ADS-B (adsb.lol) | 30 sec |
| Ham Radio | APRS-IS | Real-time |
| Satellites/ISS | wheretheiss.at | 30 sec |
| Waffle House Index | wafflehouse.com | 30 min |
| GhostNet Stations | S2 Underground GitHub | 6 hours |
| Surveillance Cameras | DeFlock/FLOCK | Scraped |

## Escape Route Features
- **Bug-Out Advisor** — Browser GPS detects your position; 24 candidate destinations scored by `POST /api/routes/suggest`; top 5 pinned on map by threat + camera exposure
- **Camera-avoidance routing** — Routes that minimize ALPR/surveillance camera exposure
- **Threat-aware planning** — Avoid active fires, severe weather, flood zones
- **Infrastructure filtering** — Bridge height/weight limits for vehicle profiles
- **Multi-egress** — Calculate 3+ alternative routes with risk scoring
- **Bug-out presets** — Pre-planned routes to saved destinations, one-click activation
- **Corridor analysis** — Show all threats/resources within N miles of route

### Bug-Out Advisor API
```
POST /api/routes/suggest?lat=LATITUDE&lon=LONGITUDE&radius_km=200&condition=general
```
`condition` values: `general`, `fire`, `earthquake`, `weather`, `civil`

Returns top 5 destinations sorted by `total_score` (lower = safer):
```json
{
  "suggestions": [
    {
      "label": "NE 60km",
      "lat": 39.54,
      "lon": -97.31,
      "bearing": "NE",
      "distance_km": 60.0,
      "threat_score": 0.0,
      "camera_score": 0.05,
      "total_score": 0.05,
      "threats": [],
      "reason": "No active threats. Low camera exposure."
    }
  ]
}
```

## Management

```bash
# Status
~/.duskfall/duskfall.sh --status

# Logs
journalctl --user -u duskfall -f

# Install feeder-relay
~/.duskfall/duskfall.sh --install-relay
```

## Related
- [WanderMage](../WanderMage) — RV trip planning platform (parent mapping stack)
- [OpenTAKServer](../../opentakserver) — TAK server for CoT data
- Access: `https://duskfall.localhost`
