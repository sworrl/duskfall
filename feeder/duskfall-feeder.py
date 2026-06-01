#!/usr/bin/env python3
"""Duskfall dump1090/readsb feeder — setup + daemon in one file.

First run: interactive setup (prompts for server + credentials, creates a
per-device API key, saves config, optionally installs a systemd service).

Subsequent runs: reads saved config and streams aircraft positions to your
Duskfall instance every 15 seconds.

Requirements: Python 3.8+, no third-party packages.
Usage:        python3 duskfall-feeder.py
"""
import getpass
import hashlib
import hmac
import json
import os
import secrets
import socket
import sys
import time
from pathlib import Path
from urllib import request as ureq
from urllib.error import HTTPError, URLError

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_DIR  = Path.home() / ".duskfall-feeder"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_SERVER   = "https://duskfall.falcontechnix.com"
DEFAULT_DUMP1090 = "http://localhost:8080"
PUSH_INTERVAL    = 15    # seconds between pushes
MAX_AGE_SEC      = 60    # ignore aircraft not heard in this many seconds


# ---------------------------------------------------------------------------
# HTTP helpers (no requests library needed)
# ---------------------------------------------------------------------------

def _http(method: str, url: str, data=None, headers=None) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = ureq.Request(url, data=body, headers=h, method=method)
    with ureq.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def _get(url, headers=None) -> dict:
    req = ureq.Request(url, headers=headers or {})
    with ureq.urlopen(req, timeout=20) as r:
        raw = r.read().decode()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}


def _post(url, data, headers=None) -> dict:
    return _http("POST", url, data, headers)


# ---------------------------------------------------------------------------
# HMAC-SHA256 request signing (required for key-creation call in hosted mode)
# Matches backend/app/core/auth.py verify_request_signature()
# ---------------------------------------------------------------------------

def _sign_headers(method: str, path: str, body: bytes, signing_key: str) -> dict:
    ts    = str(int(time.time()))
    nonce = secrets.token_hex(16)
    body_hash = hashlib.sha256(body).hexdigest()
    payload   = f"{method}\n{path}\n{ts}\n{nonce}\n{body_hash}"
    sig = hmac.new(signing_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {"X-Signature": sig, "X-Timestamp": ts, "X-Nonce": nonce}


def _signed_post(url: str, path: str, data: dict, token: str, signing_key: str) -> dict:
    body = json.dumps(data).encode()
    extra = _sign_headers("POST", path, body, signing_key)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        **extra,
    }
    req = ureq.Request(url, data=body, headers=headers, method="POST")
    with ureq.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------------------
# Setup wizard (runs once)
# ---------------------------------------------------------------------------

def setup() -> dict:
    print("\n" + "=" * 52)
    print("  Duskfall dump1090 Feeder — First-Time Setup")
    print("=" * 52 + "\n")
    print("This will connect to your Duskfall instance,")
    print("create a dedicated API key for this device,")
    print("and start streaming aircraft positions.\n")

    server = input(f"Duskfall server URL [{DEFAULT_SERVER}]: ").strip()
    server = (server or DEFAULT_SERVER).rstrip("/")

    # Verify we can reach the server
    print(f"\nChecking {server}/api/health …")
    try:
        health = _get(f"{server}/api/health")
        print(f"  OK — {health.get('app', 'duskfall')} v{health.get('version', '?')}")
    except Exception as e:
        print(f"  Cannot reach server: {e}")
        print("  Check the URL and try again.")
        sys.exit(1)

    print()
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")

    # Authenticate
    print("\nAuthenticating …")
    try:
        resp = _post(f"{server}/api/auth/login", {
            "username": username,
            "password": password,
        })
    except HTTPError as e:
        body = e.read().decode() if hasattr(e, "read") else str(e)
        print(f"  Login failed ({e.code}): {body}")
        sys.exit(1)
    except Exception as e:
        print(f"  Login error: {e}")
        sys.exit(1)

    token       = resp.get("access_token", "")
    signing_key = resp.get("signing_key", "")
    if not token:
        print("  No token in response — wrong credentials or server error.")
        sys.exit(1)
    print("  Authenticated.")

    # Create per-device API key with "adsb" scope
    hostname = socket.gethostname()
    key_name = f"dump1090-{hostname}"
    print(f"\nCreating device key '{key_name}' …")

    key_path = "/api/auth/keys"
    key_data = {
        "name": key_name,
        "permissions": ["adsb"],
        "device_type": "rpi",
    }

    try:
        if signing_key:
            # Hosted mode — request must be HMAC-signed
            key_resp = _signed_post(
                f"{server}{key_path}", key_path,
                key_data, token, signing_key,
            )
        else:
            # Local mode — no signing required
            key_resp = _post(
                f"{server}{key_path}", key_data,
                headers={"Authorization": f"Bearer {token}"},
            )
    except HTTPError as e:
        body = e.read().decode() if hasattr(e, "read") else str(e)
        print(f"  Key creation failed ({e.code}): {body}")
        sys.exit(1)
    except Exception as e:
        print(f"  Key creation error: {e}")
        sys.exit(1)

    raw_key = key_resp.get("raw_key", "")
    if not raw_key:
        print(f"  No key in response: {key_resp}")
        sys.exit(1)
    print(f"  Key created: {raw_key[:8]}…{raw_key[-4:]} (stored securely)")

    # dump1090 URL
    dump1090 = input(f"\ndump1090 URL [{DEFAULT_DUMP1090}]: ").strip()
    dump1090 = (dump1090 or DEFAULT_DUMP1090).rstrip("/")

    # Optional feeder location (improves coverage mapping server-side)
    print("\nFeeder location (optional — used for coverage mapping).")
    feeder_lat_s = input("  Latitude  (Enter to skip): ").strip()
    feeder_lon_s = input("  Longitude (Enter to skip): ").strip()
    feeder_lat = float(feeder_lat_s) if feeder_lat_s else None
    feeder_lon = float(feeder_lon_s) if feeder_lon_s else None

    config = {
        "server":     server,
        "key":        raw_key,
        "dump1090":   dump1090,
        "feeder_lat": feeder_lat,
        "feeder_lon": feeder_lon,
    }

    CONFIG_DIR.mkdir(exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    os.chmod(CONFIG_FILE, 0o600)   # key material — owner read/write only
    print(f"\nConfig saved to {CONFIG_FILE}")

    # Offer systemd service install
    if sys.platform.startswith("linux"):
        ans = input("\nInstall as systemd user service (auto-start on boot)? [y/N]: ").strip().lower()
        if ans == "y":
            _install_systemd_service()

    print("\nSetup complete. Starting feeder …\n")
    return config


# ---------------------------------------------------------------------------
# Systemd service installer
# ---------------------------------------------------------------------------

def _install_systemd_service():
    script = Path(__file__).resolve()
    python = sys.executable
    unit = f"""\
[Unit]
Description=Duskfall dump1090 Feeder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python} {script}
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
"""
    svc_dir  = Path.home() / ".config" / "systemd" / "user"
    svc_dir.mkdir(parents=True, exist_ok=True)
    svc_file = svc_dir / "duskfall-feeder.service"
    svc_file.write_text(unit)
    print(f"  Service file: {svc_file}")
    print("  Run these to enable:")
    print("    systemctl --user daemon-reload")
    print("    systemctl --user enable --now duskfall-feeder")
    print("    loginctl enable-linger $USER   # survive reboot without login")


# ---------------------------------------------------------------------------
# Feed loop
# ---------------------------------------------------------------------------

def feed(config: dict):
    server     = config["server"]
    key        = config["key"]
    dump1090   = config["dump1090"]
    feeder_lat = config.get("feeder_lat")
    feeder_lon = config.get("feeder_lon")

    ingest_url = f"{server}/api/adsb/ingest"
    key_header = {"X-Device-Key": key}

    print(f"Duskfall feeder running")
    print(f"  source   : {dump1090}")
    print(f"  target   : {ingest_url}")
    if feeder_lat and feeder_lon:
        print(f"  location : {feeder_lat:.4f}, {feeder_lon:.4f}")
    print("  interval : every %ds\n" % PUSH_INTERVAL)

    backoff = 1
    consecutive_errors = 0

    while True:
        try:
            # dump1090 exposes aircraft at /data/aircraft.json (FA/PiAware)
            # or /data.json (older builds).  readsb uses /data/aircraft.json.
            raw = None
            for path in ("/data/aircraft.json", "/data.json"):
                try:
                    raw = _get(f"{dump1090}{path}")
                    break
                except Exception:
                    continue

            if raw is None:
                raise ConnectionError(f"No aircraft.json found at {dump1090}")

            aircraft = raw.get("aircraft", raw if isinstance(raw, list) else [])

            # Filter to aircraft seen recently and with a position
            fresh = [
                ac for ac in aircraft
                if isinstance(ac.get("seen", 9999), (int, float))
                and ac["seen"] <= MAX_AGE_SEC
                and ac.get("lat") is not None
                and ac.get("lon") is not None
            ]

            if fresh:
                resp = _post(ingest_url, {
                    "aircraft":   fresh,
                    "feeder_lat": feeder_lat,
                    "feeder_lon": feeder_lon,
                }, headers=key_header)

                ts  = time.strftime("%H:%M:%S")
                acc = resp.get("accepted", 0)
                rej = resp.get("rejected", 0)
                print(f"[{ts}] {len(fresh):3d} ac pushed  accepted={acc}  rejected={rej}")

            backoff = 1
            consecutive_errors = 0

        except HTTPError as e:
            consecutive_errors += 1
            msg = ""
            try:
                msg = e.read().decode()
            except Exception:
                pass
            print(f"[!] HTTP {e.code}: {msg or e.reason}", file=sys.stderr)
            if e.code == 401:
                print("[!] Auth failure — delete config and re-run setup:", file=sys.stderr)
                print(f"    rm {CONFIG_FILE}", file=sys.stderr)
                sys.exit(1)
            if e.code == 403:
                print("[!] Key lacks 'adsb' permission — re-run setup.", file=sys.stderr)
                sys.exit(1)
            backoff = min(backoff * 2, 120)

        except (URLError, ConnectionError, OSError) as e:
            consecutive_errors += 1
            print(f"[!] Connection error: {e}", file=sys.stderr)
            backoff = min(backoff * 2, 120)

        except Exception as e:
            consecutive_errors += 1
            print(f"[!] Unexpected error: {e}", file=sys.stderr)
            backoff = min(backoff * 2, 120)

        wait = PUSH_INTERVAL if backoff == 1 else backoff
        time.sleep(wait)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except Exception as e:
            print(f"Config corrupt ({e}) — re-running setup.")
            config = setup()
    else:
        config = setup()

    feed(config)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nFeeder stopped.")
        sys.exit(0)
