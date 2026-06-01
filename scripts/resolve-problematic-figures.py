"""Resolve 'problematic figures' watchlist additions via adsbdb.com.

Targets: heads of state, sanctioned oligarchs, royal flights, and other
high-visibility individuals not on the May 2026 celebrityprivatejettracker
list. Anything that adsbdb can't resolve is emitted to data_needed so the
operator can fill in manually.
"""
import json
import sys
import time
import urllib.parse
import urllib.request

# (name, reg, model, category, notes)
CANDIDATES = [
    # ---- Russian state — Putin's Special Air Detachment ----
    ("Putin / Special Flight Squadron",  "RA-96022", "Il-96-300PU", "POLITICIAN", "Russian presidential transport"),
    ("Putin / Special Flight Squadron",  "RA-96023", "Il-96-300",   "POLITICIAN", "Russian presidential transport"),
    ("Putin / Special Flight Squadron",  "RA-96024", "Il-96-300PU", "POLITICIAN", "Russian presidential transport"),
    ("Putin / Special Flight Squadron",  "RA-96025", "Il-96-300",   "POLITICIAN", "Russian presidential transport"),
    ("Putin / Special Flight Squadron",  "RA-96017", "Il-96-300",   "POLITICIAN", "Russian presidential transport"),
    # ---- Russian oligarchs (sanctioned) ----
    ("Roman Abramovich",                 "P4-BDL",   "B787-8",      "BILLIONAIRE", "Sanctioned — DOJ seizure warrant"),
    ("Roman Abramovich",                 "LX-RAY",   "G650ER",      "BILLIONAIRE", "Sanctioned — DOJ seizure warrant"),
    # ---- Saudi Royal Flight (MBS / King Salman) ----
    ("Saudi Royal Flight (MBS / King)",  "HZ-HM1",   "B747-468",    "POLITICIAN", "Saudi royal flight"),
    ("Saudi Royal Flight",               "HZ-HM1B",  "B747SP",      "POLITICIAN", "Saudi royal flight"),
    ("Saudi Royal Flight",               "HZ-HM1C",  "B747-300",    "POLITICIAN", "Saudi royal flight"),
    # ---- Chinese state (Xi Jinping uses Air China 747) ----
    ("Xi Jinping / Air China",           "B-2479",   "B747-8I",     "POLITICIAN", "Chinese state transport"),
    ("Xi Jinping / Air China",           "B-2480",   "B747-8I",     "POLITICIAN", "Chinese state transport"),
    # ---- Other controversial / heavy-emission celebs ----
    ("Madonna",                          "N804MM",   "Gulfstream",  "CELEBRITY", "Pop / Material Girl"),
    ("Beyoncé Knowles",                  "N240MJ",   "Bombardier",  "CELEBRITY", "Parkwood Entertainment"),
    ("Rihanna",                          "N818RR",   "Gulfstream",  "CELEBRITY", "Fenty"),
    ("Justin Bieber",                    "N122JB",   "Gulfstream",  "CELEBRITY", "Pop"),
    ("Kanye West / Ye",                  "N12KW",    "Gulfstream",  "CELEBRITY", "Donda / Yeezy"),
    ("Lil Wayne",                        "N9LW",     "Gulfstream",  "CELEBRITY", "Young Money"),
    ("50 Cent",                          "N550CC",   "Gulfstream",  "CELEBRITY", "G-Unit"),
    ("Stephen A. Smith",                 "N18SS",    "Citation",    "CELEBRITY", "ESPN"),
    # ---- Other billionaires not on the May 2026 list ----
    ("George Soros / Open Society",      "N777SS",   "Gulfstream",  "BILLIONAIRE", "Open Society Foundations"),
    ("Charles Koch / Koch Industries",   "N1WK",     "Falcon",      "BILLIONAIRE", "Koch Industries"),
    ("Bernard Arnault / LVMH",           "F-GVMH",   "Falcon",      "BILLIONAIRE", "LVMH"),
    ("Carlos Slim",                      "XB-FVS",   "Gulfstream",  "BILLIONAIRE", "Mexican telecom"),
    ("Mukesh Ambani / Reliance",         "VT-AVE",   "Falcon",      "BILLIONAIRE", "Reliance Industries"),
    ("Warren Buffett / Berkshire",       "N1BV",     "Falcon",      "BILLIONAIRE", "Berkshire Hathaway / NetJets"),
    # ---- Heavy-emission climate-critical celebs ----
    ("Justin Timberlake",                "N515JT",   "Gulfstream",  "CELEBRITY", "Pop"),
    ("Steven Tyler / Aerosmith",         "N888ST",   "Falcon",      "CELEBRITY", "Aerosmith"),
    ("Robert Kraft / Patriots",          "N1NE",     "Boeing",      "BILLIONAIRE", "NFL Patriots owner"),
    # ---- Trump orbit (additional aircraft) ----
    ("Trump Org / Eric Trump",           "N76TF",    "Cessna",      "POLITICIAN", "Trump Org"),
]

API = "https://api.adsbdb.com/v0/aircraft/{reg}"


def resolve(reg: str) -> dict | None:
    try:
        url = API.format(reg=urllib.parse.quote(reg))
        req = urllib.request.Request(url, headers={"User-Agent": "duskfall-resolver/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
            ac = data.get("response", {}).get("aircraft")
            return ac if ac else None
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
            "hex": ac["mode_s"].lower(),
            "reg": tail,
            "name": name,
            "category": category,
            "type": ac.get("icao_type") or "",
            "model": ac.get("type") or model,
            "owner": ac.get("registered_owner") or "",
            "note": notes,
        })
        print(f"  OK   {tail:10s} {ac['mode_s']:8s} -> {name}", file=sys.stderr)
    else:
        reason = ac.get("_error") if ac else "not in adsbdb"
        data_needed.append({
            "reg": tail, "name": name, "category": category,
            "model": model, "note": notes, "reason": reason,
        })
        print(f"  MISS {tail:10s}          -> {name}  ({reason})", file=sys.stderr)

print(json.dumps({"resolved": resolved, "data_needed": data_needed}, indent=2))
