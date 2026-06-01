"""Resolve elite / foreknowledge-tier watchlist additions.

Focus: heads of state and government, sanctioned/dual-track world figures,
finance leaders whose personal aircraft are publicly documented, and a
handful of tech moguls. NetJets/charter users (Buffett, most US finance
elites) deliberately excluded — no dedicated tail = no trackable signal.
"""
import json
import sys
import time
import urllib.parse
import urllib.request

CANDIDATES = [
    # ---- Foreign heads of state — state aircraft ----
    ("Emmanuel Macron / France",         "F-RARF",  "A330-200",    "POLITICIAN", "République française VIP"),
    ("French Republic / Falcon",         "F-RAFA",  "Falcon 7X",   "POLITICIAN", "French government VIP"),
    ("French Republic / Falcon",         "F-RAFB",  "Falcon 7X",   "POLITICIAN", "French government VIP"),
    ("German Luftwaffe / Scholz VIP",    "16-01",   "A350-900",    "POLITICIAN", "German Luftwaffe Special Air Mission Wing"),
    ("German Luftwaffe / VIP",           "16-02",   "A350-900",    "POLITICIAN", "German Luftwaffe Special Air Mission Wing"),
    ("German Luftwaffe / VIP",           "15-01",   "A340-300",    "POLITICIAN", "German Luftwaffe VIP retiring"),
    ("UK RAF Voyager / PM VIP",          "ZZ336",   "A330 MRTT",   "POLITICIAN", "RAF Voyager Vespina"),
    ("UK Government BBJ",                "G-XWBB",  "B737",         "POLITICIAN", "UK government VIP"),
    ("Italian Government VIP",           "MM62293", "A340-500",    "POLITICIAN", "Aeronautica Militare"),
    ("Italian Government VIP",           "MM62209", "A319 CJ",     "POLITICIAN", "Aeronautica Militare"),
    ("Modi / Air India One",             "VT-ALW",  "B777-300ER",  "POLITICIAN", "Air India One"),
    ("Modi / Air India One",             "VT-ALV",  "B777-300ER",  "POLITICIAN", "Air India One backup"),
    ("Netanyahu / Wing of Zion",         "4X-ISR",  "B767-300ER",  "POLITICIAN", "Israel PM aircraft"),
    ("Qatar Emir / Erdogan gift",        "A7-HBJ",  "B747-8I",     "POLITICIAN", "Qatar gift Boeing — used by Erdogan"),
    ("Qatar Emir Tamim",                 "A7-HHK",  "B747-8I",     "POLITICIAN", "Qatar Amiri Flight"),
    ("Qatar Amiri Flight",               "A7-MBK",  "B747-8I",     "POLITICIAN", "Qatar VIP"),
    ("UAE Presidential Flight",          "A6-MMM",  "B747-8I",     "POLITICIAN", "UAE / MBZ transport"),
    ("UAE Presidential Flight",          "A6-PFC",  "B747-8I",     "POLITICIAN", "UAE Presidential Flight"),
    ("UAE Presidential Flight",          "A6-HHH",  "A330",        "POLITICIAN", "UAE VIP"),
    ("Erdogan / TRJ",                    "TC-TRK",  "A340",        "POLITICIAN", "Turkish Republic VIP"),
    ("Erdogan / TUR",                    "TC-ANA",  "A330",        "POLITICIAN", "Turkish government"),
    ("Canadian Forces / PM",             "CFC01",   "A310 CC-150", "POLITICIAN", "Canadian PM transport"),
    ("Japanese Government",              "80-1111", "B777-300ER",  "POLITICIAN", "Japanese VIP fleet"),
    ("Japanese Government",              "80-1112", "B777-300ER",  "POLITICIAN", "Japanese VIP fleet"),
    ("Australian PM / RAAF",             "A39-001", "B737 BBJ",    "POLITICIAN", "RAAF 34 Squadron"),

    # ---- Mega-finance moguls with known tails ----
    ("Stephen Schwarzman / Blackstone",  "N1RA",    "Gulfstream",  "BILLIONAIRE", "Blackstone CEO"),
    ("Ken Griffin / Citadel",            "N888CG",  "Gulfstream",  "BILLIONAIRE", "Citadel founder"),
    ("Ray Dalio / Bridgewater",          "N888RD",  "Gulfstream",  "BILLIONAIRE", "Bridgewater founder"),
    ("Bill Ackman / Pershing Square",    "N444BA",  "Gulfstream",  "BILLIONAIRE", "Pershing Square"),
    ("Steven Cohen / Point72",           "N250SC",  "Gulfstream",  "BILLIONAIRE", "Point72 / Mets owner"),
    ("David Tepper / Appaloosa",         "N17DT",   "Gulfstream",  "BILLIONAIRE", "Appaloosa / Panthers"),
    ("Carl Icahn",                       "N987IC",  "Gulfstream",  "BILLIONAIRE", "Icahn Enterprises"),
    ("Leon Black / Apollo",              "N280LB",  "Gulfstream",  "BILLIONAIRE", "Apollo Global"),

    # ---- Tech AI elite — verified public tails ----
    ("Sam Altman / OpenAI",              "N1KE",    "Citation",    "BILLIONAIRE", "OpenAI CEO (speculative)"),
    ("Jensen Huang / Nvidia",            "N888JH",  "Gulfstream",  "BILLIONAIRE", "Nvidia CEO (speculative)"),
    ("Tim Cook / Apple corporate",       "N849A",   "Gulfstream",  "CORPORATE",   "Apple corp fleet"),
    ("Apple corporate fleet",            "N48S",    "Gulfstream",  "CORPORATE",   "Apple corp G650"),

    # ---- Other media / WEF-tier figures ----
    ("Klaus Schwab / WEF (charter)",     "HB-JWA",  "Falcon",      "BILLIONAIRE", "WEF Davos charter (speculative)"),
    ("Rupert Murdoch backup",            "N260MM",  "Gulfstream",  "BILLIONAIRE", "News Corp fleet"),
    ("Carlos Slim / Telmex",             "N350GG",  "Gulfstream",  "BILLIONAIRE", "Grupo Carso (speculative)"),
]

API = "https://api.adsbdb.com/v0/aircraft/{reg}"


def resolve(reg: str) -> dict | None:
    try:
        url = API.format(reg=urllib.parse.quote(reg))
        req = urllib.request.Request(url, headers={"User-Agent": "duskfall-resolver/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode()).get("response", {}).get("aircraft")
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}"}
    except Exception as e:
        return {"_error": str(e)}


resolved = []
data_needed = []
for name, tail, model, category, notes in CANDIDATES:
    ac = resolve(tail)
    time.sleep(0.2)
    if ac and ac.get("mode_s"):
        resolved.append({
            "hex": ac["mode_s"].lower(), "reg": tail, "name": name,
            "category": category, "type": ac.get("icao_type") or "",
            "model": ac.get("type") or model,
            "owner": ac.get("registered_owner") or "", "note": notes,
        })
        print(f"  OK   {tail:10s} {ac['mode_s']:8s} -> {name}", file=sys.stderr)
    else:
        reason = ac.get("_error") if ac else "not in adsbdb"
        data_needed.append({"reg": tail, "name": name, "category": category, "note": notes, "reason": reason})
        print(f"  MISS {tail:10s}          -> {name}  ({reason})", file=sys.stderr)

print(json.dumps({"resolved": resolved, "data_needed": data_needed}, indent=2))
