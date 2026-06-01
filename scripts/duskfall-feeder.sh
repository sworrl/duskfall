#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# Duskfall ADS-B Feeder — Detects local ADS-B decoder and forwards
# aircraft data to a Duskfall server via the Contribute API.
#
# Supports: readsb, dump1090-fa, dump1090, fr24feed
# Auth: X-Device-Key header (create key in Duskfall Account panel)
#
# Install: curl -sL <repo>/scripts/duskfall-feeder.sh | bash -s -- --install
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

CONFIG_FILE="${HOME}/.config/duskfall-feeder.conf"
SERVICE_NAME="duskfall-feeder"
INTERVAL=30  # seconds between pushes

# ── Defaults ──
DUSKFALL_URL=""
DEVICE_KEY=""
MAIN_SERVER_URL=""
MAIN_SERVER_KEY=""
AIRCRAFT_JSON=""
LAT=""
LON=""
ELEVATION=""

usage() {
    cat <<EOF
Duskfall ADS-B Feeder

Usage:
  duskfall-feeder.sh --install     Auto-detect decoder, configure, install service
  duskfall-feeder.sh --run         Run once (push current aircraft data)
  duskfall-feeder.sh --daemon      Run continuously (used by systemd)
  duskfall-feeder.sh --status      Show feeder status
  duskfall-feeder.sh --uninstall   Remove service and config

Config: ${CONFIG_FILE}
EOF
}

# ── Auto-detect ADS-B decoder ──
detect_decoder() {
    local json_paths=(
        "/run/readsb/aircraft.json"
        "/run/dump1090-fa/aircraft.json"
        "/run/dump1090/aircraft.json"
        "/var/run/readsb/aircraft.json"
        "/tmp/dump1090-fa/aircraft.json"
    )

    for path in "${json_paths[@]}"; do
        if [[ -f "$path" ]]; then
            echo "$path"
            return 0
        fi
    done

    # Check for fr24feed
    if command -v fr24feed &>/dev/null; then
        local fr24_json="/var/lib/fr24feed/aircraft.json"
        if [[ -f "$fr24_json" ]]; then
            echo "$fr24_json"
            return 0
        fi
    fi

    return 1
}

# ── Get receiver location from decoder config ──
detect_location() {
    # readsb config
    if [[ -f /etc/default/readsb ]]; then
        LAT=$(grep -oP '(?<=--lat )[0-9.-]+' /etc/default/readsb 2>/dev/null || true)
        LON=$(grep -oP '(?<=--lon )[0-9.-]+' /etc/default/readsb 2>/dev/null || true)
    fi

    # dump1090-fa config
    if [[ -z "$LAT" && -f /etc/default/dump1090-fa ]]; then
        LAT=$(grep -oP '(?<=--lat )[0-9.-]+' /etc/default/dump1090-fa 2>/dev/null || true)
        LON=$(grep -oP '(?<=--lon )[0-9.-]+' /etc/default/dump1090-fa 2>/dev/null || true)
    fi

    # fr24feed config
    if [[ -z "$LAT" && -f /etc/fr24feed.ini ]]; then
        LAT=$(grep -oP '(?<=latitude=)[0-9.-]+' /etc/fr24feed.ini 2>/dev/null || true)
        LON=$(grep -oP '(?<=longitude=)[0-9.-]+' /etc/fr24feed.ini 2>/dev/null || true)
    fi

    # Auto-acquire elevation
    if [[ -n "$LAT" && -n "$LON" && -z "$ELEVATION" ]]; then
        ELEVATION=$(curl -sf "https://api.open-elevation.com/api/v1/lookup?locations=${LAT},${LON}" \
            | python3 -c "import sys,json; print(json.load(sys.stdin)['results'][0]['elevation'])" 2>/dev/null || echo "")
    fi
}

# ── Convert aircraft.json to Duskfall feed events ──
convert_and_push() {
    local json_file="$1"
    local url="$2"
    local key="$3"

    if [[ ! -f "$json_file" ]]; then
        echo "[feeder] aircraft.json not found: $json_file"
        return 1
    fi

    # Use python3 to convert and POST
    python3 - "$json_file" "$url" "$key" "$LAT" "$LON" "$ELEVATION" <<'PYEOF'
import sys, json, time
try:
    from urllib.request import Request, urlopen
except ImportError:
    print("[feeder] Python urllib not available")
    sys.exit(1)

json_file, url, key = sys.argv[1], sys.argv[2], sys.argv[3]
rx_lat, rx_lon, rx_elev = sys.argv[4], sys.argv[5], sys.argv[6]

with open(json_file) as f:
    data = json.load(f)

aircraft_list = data.get("aircraft", data if isinstance(data, list) else [])
events = []

for ac in aircraft_list:
    lat = ac.get("lat")
    lon = ac.get("lon")
    if lat is None or lon is None:
        continue

    hex_code = ac.get("hex", "").strip()
    if not hex_code:
        continue

    callsign = (ac.get("flight") or ac.get("r") or hex_code).strip()
    alt = ac.get("alt_baro") or ac.get("altitude") or 0
    if isinstance(alt, str):
        alt = 0
    speed = ac.get("gs") or ac.get("speed") or 0
    heading = ac.get("track") or ac.get("heading") or 0
    squawk = ac.get("squawk", "")
    reg = ac.get("r", "")
    ac_type = ac.get("t", "")

    events.append({
        "uid": f"feeder-adsb-{hex_code}",
        "feed_type": "adsb",
        "title": f"{callsign} ({ac_type})" if ac_type else callsign,
        "description": f"Reg:{reg} Squawk:{squawk} Alt:{alt}ft Spd:{speed}kts Hdg:{heading}",
        "latitude": lat,
        "longitude": lon,
        "altitude": float(alt) * 0.3048 if alt else None,  # ft to m
        "severity": "info",
        "source_url": f"adsb-feeder:{rx_lat},{rx_lon}",
    })

if not events:
    print(f"[feeder] No aircraft with positions in {json_file}")
    sys.exit(0)

payload = json.dumps(events).encode()
req = Request(
    f"{url}/api/contribute/feed",
    data=payload,
    headers={
        "Content-Type": "application/json",
        "X-Device-Key": key,
    },
    method="POST",
)

try:
    resp = urlopen(req, timeout=15)
    result = json.loads(resp.read())
    print(f"[feeder] Pushed {result.get('accepted', 0)} aircraft to {url}")
except Exception as e:
    print(f"[feeder] Push failed: {e}")
    sys.exit(1)
PYEOF
}

# ── Load config ──
load_config() {
    if [[ -f "$CONFIG_FILE" ]]; then
        source "$CONFIG_FILE"
    fi
}

# ── Save config ──
save_config() {
    mkdir -p "$(dirname "$CONFIG_FILE")"
    cat > "$CONFIG_FILE" <<EOF
# Duskfall ADS-B Feeder Configuration
DUSKFALL_URL="${DUSKFALL_URL}"
DEVICE_KEY="${DEVICE_KEY}"
MAIN_SERVER_URL="${MAIN_SERVER_URL}"
MAIN_SERVER_KEY="${MAIN_SERVER_KEY}"
AIRCRAFT_JSON="${AIRCRAFT_JSON}"
LAT="${LAT}"
LON="${LON}"
ELEVATION="${ELEVATION}"
INTERVAL=${INTERVAL}
EOF
    chmod 600 "$CONFIG_FILE"
}

# ── Install ──
do_install() {
    echo "=== Duskfall ADS-B Feeder Setup ==="
    echo

    # Detect decoder
    echo "[1/5] Detecting ADS-B decoder..."
    if AIRCRAFT_JSON=$(detect_decoder); then
        echo "  Found: ${AIRCRAFT_JSON}"
    else
        echo "  No ADS-B decoder detected."
        read -rp "  Path to aircraft.json: " AIRCRAFT_JSON
    fi

    # Detect location
    echo "[2/5] Detecting receiver location..."
    detect_location
    if [[ -n "$LAT" && -n "$LON" ]]; then
        echo "  Location: ${LAT}, ${LON} (elevation: ${ELEVATION:-unknown}m)"
    else
        read -rp "  Latitude: " LAT
        read -rp "  Longitude: " LON
        detect_location  # Try elevation again
    fi

    # Duskfall server
    echo "[3/5] Duskfall server configuration..."
    read -rp "  Local Duskfall URL (e.g. http://192.168.1.10:8500, or blank to skip): " DUSKFALL_URL
    if [[ -n "$DUSKFALL_URL" ]]; then
        read -rp "  Device API key for local server: " DEVICE_KEY
    fi

    # Main server
    echo "[4/5] Main server (duskfall.falcontechnix.com)..."
    read -rp "  Send data to main server? (y/n): " yn
    if [[ "$yn" =~ ^[Yy] ]]; then
        MAIN_SERVER_URL="https://duskfall.falcontechnix.com"
        read -rp "  Device API key for main server: " MAIN_SERVER_KEY
    fi

    save_config
    echo "  Config saved to ${CONFIG_FILE}"

    # Install systemd service
    echo "[5/5] Installing systemd service..."
    local script_path
    script_path="$(readlink -f "$0")"

    mkdir -p "${HOME}/.config/systemd/user"
    cat > "${HOME}/.config/systemd/user/${SERVICE_NAME}.service" <<SVCEOF
[Unit]
Description=Duskfall ADS-B Feeder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/bin/bash ${script_path} --daemon
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
SVCEOF

    systemctl --user daemon-reload
    systemctl --user enable --now "${SERVICE_NAME}.service"
    echo
    echo "=== Duskfall Feeder installed and running ==="
    echo "  Status: systemctl --user status ${SERVICE_NAME}"
    echo "  Logs:   journalctl --user -u ${SERVICE_NAME} -f"
}

# ── Run once ──
do_run() {
    load_config
    if [[ -n "$DUSKFALL_URL" && -n "$DEVICE_KEY" ]]; then
        convert_and_push "$AIRCRAFT_JSON" "$DUSKFALL_URL" "$DEVICE_KEY"
    fi
    if [[ -n "$MAIN_SERVER_URL" && -n "$MAIN_SERVER_KEY" ]]; then
        convert_and_push "$AIRCRAFT_JSON" "$MAIN_SERVER_URL" "$MAIN_SERVER_KEY"
    fi
}

# ── Daemon ──
do_daemon() {
    load_config
    echo "[feeder] Starting daemon (interval: ${INTERVAL}s)"
    echo "[feeder] Aircraft JSON: ${AIRCRAFT_JSON}"
    while true; do
        do_run || true
        sleep "$INTERVAL"
    done
}

# ── Status ──
do_status() {
    load_config
    echo "Duskfall ADS-B Feeder"
    echo "  Config:        ${CONFIG_FILE}"
    echo "  Aircraft JSON: ${AIRCRAFT_JSON:-not set}"
    echo "  Location:      ${LAT:-?}, ${LON:-?} (${ELEVATION:-?}m)"
    echo "  Local server:  ${DUSKFALL_URL:-not configured}"
    echo "  Main server:   ${MAIN_SERVER_URL:-not configured}"
    if [[ -f "$AIRCRAFT_JSON" ]]; then
        local count
        count=$(python3 -c "import json; print(len(json.load(open('${AIRCRAFT_JSON}')).get('aircraft',[])))" 2>/dev/null || echo "?")
        echo "  Live aircraft: ${count}"
    fi
    systemctl --user status "${SERVICE_NAME}" 2>/dev/null || echo "  Service: not installed"
}

# ── Uninstall ──
do_uninstall() {
    systemctl --user stop "${SERVICE_NAME}" 2>/dev/null || true
    systemctl --user disable "${SERVICE_NAME}" 2>/dev/null || true
    rm -f "${HOME}/.config/systemd/user/${SERVICE_NAME}.service"
    systemctl --user daemon-reload
    rm -f "$CONFIG_FILE"
    echo "Duskfall Feeder uninstalled"
}

# ── Main ──
case "${1:-}" in
    --install)   do_install ;;
    --run)       do_run ;;
    --daemon)    do_daemon ;;
    --status)    do_status ;;
    --uninstall) do_uninstall ;;
    *)           usage ;;
esac
