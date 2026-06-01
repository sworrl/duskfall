"""Resolve celebrity tail numbers to ICAO hex codes via adsbdb.com.

Reads a curated list of <name, tail, model, category, source> entries,
queries https://api.adsbdb.com/v0/aircraft/<reg> for each, and emits a
Python dict ready to paste into vip_aircraft.VIP_HEX_CODES.

Tail-number list sourced from CelebrityPrivateJetTracker.com (May 2026),
cross-checked against publicly-republished Sweeney lists where possible.
"""
import json
import sys
import time
import urllib.parse
import urllib.request

# (celebrity, tail, model, category, notes)
# category: BILLIONAIRE | CELEBRITY | CORPORATE | POLITICIAN
CELEBS = [
    # --- Billionaires (tech / finance / industry) ---
    ("Elon Musk",        "N628TS", "Gulfstream G650ER",       "BILLIONAIRE", "Falcon Landing LLC — Tesla/SpaceX"),
    ("Bill Gates",       "N887WM", "Gulfstream G650",         "BILLIONAIRE", "Cascade Investment #1"),
    ("Bill Gates",       "N194WM", "Gulfstream G650",         "BILLIONAIRE", "Cascade Investment #2"),
    ("Jeff Bezos",       "N758PB", "Gulfstream G650",         "BILLIONAIRE", "Poplar Glen LLC"),
    ("Mark Zuckerberg",  "N68885", "Gulfstream G650",         "BILLIONAIRE", "Solairus Aviation"),
    ("Larry Ellison",    "N817GS", "Gulfstream G650",         "BILLIONAIRE", "Oracle exec"),
    ("Eric Schmidt",     "N652WE", "Gulfstream G650",         "BILLIONAIRE", "ex-Google"),
    ("Sergey Brin",      "N232G",  "Gulfstream G650",         "BILLIONAIRE", "Google co-founder"),
    ("Marc Benioff",     "N650HA", "Gulfstream G650",         "BILLIONAIRE", "Salesforce CEO"),
    ("Mark Cuban",       "N921MT", "Bombardier Global Express","BILLIONAIRE", "Mavericks owner"),
    ("Michael Bloomberg","N5MV",   "Dassault Falcon 900",     "BILLIONAIRE", "Bloomberg LP #1"),
    ("Michael Bloomberg","N47EG",  "Dassault Falcon 900",     "BILLIONAIRE", "Bloomberg LP #2"),
    ("Michael Bloomberg","N8AG",   "Dassault Falcon 900",     "BILLIONAIRE", "Bloomberg LP #3"),
    ("David Geffen",     "N221DG", "Gulfstream G650",         "BILLIONAIRE", "DreamWorks co-founder"),
    ("Ronald Perelman",  "N838MF", "Gulfstream G650",         "BILLIONAIRE", "MacAndrews & Forbes"),
    ("Peter Thiel",      "N878DB", "Gulfstream V",            "BILLIONAIRE", "Founders Fund"),
    ("Steve Ballmer",    "N709DS", "Gulfstream G650",         "BILLIONAIRE", "Clippers owner / ex-MSFT"),
    ("Phil Knight",      "N1KE",   "Gulfstream G650",         "BILLIONAIRE", "Nike founder"),
    ("Jerry Jones",      "N1DC",   "Gulfstream V",            "BILLIONAIRE", "Cowboys owner"),
    ("George Lucas",     "N138GL", "Gulfstream V",            "BILLIONAIRE", "Lucasfilm founder"),
    ("Michael Jordan",   "N236MJ", "Gulfstream V",            "BILLIONAIRE", "Hornets owner / Air Jordan"),
    ("Rupert Murdoch",   "N898NC", "Gulfstream G650",         "BILLIONAIRE", "News Corp"),
    ("Steven Spielberg", "N900KS", "Gulfstream G650",         "BILLIONAIRE", "Director / Amblin"),
    ("Dan Bilzerian",    "N701DB", "Gulfstream IV",           "BILLIONAIRE", "Poker / Ignite"),
    ("Steve Wynn",       "N88WR",  "Gulfstream V",            "BILLIONAIRE", "Wynn Resorts"),
    # --- Politicians ---
    ("Donald Trump",     "N757AF", "Boeing 757-200",          "POLITICIAN",  "Trump Force One"),
    ("Ron DeSantis",     "N943FL", "Cessna Citation Latitude","POLITICIAN",  "FL Gov / political travel"),
    # --- Celebrities (music / film / sports) ---
    ("Taylor Swift",     "N898TS", "Dassault Falcon 900",     "CELEBRITY",   "Sold 2024, kept here for historic"),
    ("Drake",            "N767CJ", "Boeing 767-200",          "CELEBRITY",   "Air Drake"),
    ("Jay-Z",            "N444SC", "Gulfstream V",            "CELEBRITY",   "Roc Nation"),
    ("Kim Kardashian",   "N1980K", "Gulfstream G650",         "CELEBRITY",   "Kardashian Air"),
    ("Kylie Jenner",     "N810KJ", "Bombardier Global 7500",  "CELEBRITY",   "Kylie Air"),
    ("Travis Scott",     "N713TS", "Embraer E190",            "CELEBRITY",   "Cactus Jack"),
    ("Oprah Winfrey",    "N540W",  "Gulfstream G650",         "CELEBRITY",   "Harpo"),
    ("Tom Cruise",       "N350XX", "Bombardier Challenger 350","CELEBRITY",  "Top Gun era"),
    ("Lady Gaga",        "N474D",  "Gulfstream V",            "CELEBRITY",   "Haus of Gaga"),
    ("Jim Carrey",       "N162JC", "Gulfstream V",            "CELEBRITY",   "Actor"),
    ("Matt Damon",       "N444WT", "Bombardier Global 7500",  "CELEBRITY",   "Actor"),
    ("Mark Wahlberg",    "N143MW", "Bombardier Global Express","CELEBRITY",  "Actor / Wahlburgers"),
    ("Harrison Ford",    "N6GU",   "Cessna Citation Sovereign","CELEBRITY",  "Pilot himself"),
    ("Diddy",            "N1969C", "Gulfstream V",            "CELEBRITY",   "Bad Boy / Combs Enterprises"),
    ("Kenny Chesney",    "N7KC",   "Dassault Falcon 900",     "CELEBRITY",   "Country"),
    ("Kid Rock",         "N71KR",  "Bombardier Challenger 600","CELEBRITY",  "Rock/country"),
    ("Luke Bryan",       "N506AB", "Learjet 60",              "CELEBRITY",   "Country"),
    ("Blake Shelton",    "N958TB", "Gulfstream IV",           "CELEBRITY",   "Country / The Voice"),
    ("Alex Rodriguez",   "N313AR", "Gulfstream IV",           "CELEBRITY",   "MLB / A-Rod Corp"),
    ("Floyd Mayweather", "N151SD", "Gulfstream IV",           "CELEBRITY",   "Boxer / TMT"),
    ("Dr. Phil",         "N4DP",   "Gulfstream IV",           "CELEBRITY",   "Peteski Productions"),
    ("Judge Judy",       "N555QB", "Cessna Citation 750",     "CELEBRITY",   "Judge Sheindlin"),
    ("Tommy Hilfiger",   "N818TH", "Dassault Falcon 900",     "CELEBRITY",   "Fashion"),
    # --- Corporate fleets ---
    ("Google / Alphabet","N10XG",  "Gulfstream V",            "CORPORATE",   "Alphabet exec"),
    ("Nike Corporation", "N6453",  "Gulfstream G650",         "CORPORATE",   "Nike exec"),
    ("Under Armour",     "N96UA",  "Gulfstream V",            "CORPORATE",   "Plank fleet"),
    ("Playboy",          "N950PB", "Bombardier Global Express","CORPORATE",  "Playboy Enterprises"),
    ("Caesars Palace",   "N898CE", "Gulfstream V",            "CORPORATE",   "Casino"),
    # --- International / data-needed ---
    ("Elton John",       "M-EDZE", "Bombardier Global Express","CELEBRITY",  "Isle of Man — non-N reg, hex needs manual lookup"),
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
    except Exception as e:
        return {"_error": str(e)}


resolved = []
data_needed = []

for name, tail, model, category, notes in CELEBS:
    ac = resolve(tail)
    time.sleep(0.15)  # be polite to the free API
    if ac and ac.get("mode_s"):
        resolved.append({
            "hex": ac["mode_s"].lower(),
            "registration": tail,
            "name": name,
            "category": category,
            "type": ac.get("icao_type") or "",
            "model": ac.get("type") or model,
            "owner": ac.get("registered_owner") or "",
            "note": notes,
            "source": "celebrityprivatejettracker.com + adsbdb.com (May 2026)",
        })
        print(f"  OK   {tail:8s} {ac['mode_s']:8s} -> {name}", file=sys.stderr)
    else:
        data_needed.append({
            "registration": tail,
            "name": name,
            "category": category,
            "model": model,
            "note": notes,
            "reason": ac.get("_error", "no mode_s in adsbdb response") if ac else "not in adsbdb",
        })
        print(f"  MISS {tail:8s}            -> {name}", file=sys.stderr)

print(json.dumps({"resolved": resolved, "data_needed": data_needed}, indent=2))
