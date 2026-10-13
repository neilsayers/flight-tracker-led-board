"""Works out what kind of aircraft something is (airliner, glider, helicopter...), to pick its icon and colour.

Several signals are combined, because no single one is reliable: ADS-B broadcasts an emitter category, but
military aircraft often leave it blank and gliders can report "light aircraft". Order matters, most specific first.
"""
from __future__ import annotations

import re

from .aircraft_types import HELI_TYPES, role_label

GLIDER_TYPES = {"GLID"}
BALLOON_TYPES = {"BALL"}
FIGHTER_TYPES = {"EUFI", "F35", "F16", "F15", "F18H", "F18S", "F22", "A10", "TORN", "JAGR", "HAWK", "M346", "T38",
                 "F5", "MG29", "SU27", "SU30", "SU35", "RFAL", "GR4", "F14", "F4", "B2", "T6", "TUCA"}
MILITARY_TYPES = FIGHTER_TYPES | {"C17", "C130", "C30J", "A400", "K35R", "E3TF", "E3CF", "B52", "C5M", "P8", "EH10",
                                  "H60", "CH47", "NH90", "H64", "LYNX", "WAH6", "PUMA", "A332"} - {"A332"}
MILITARY_ROLES = {"Royal Navy", "British Army", "Royal Air Force", "Irish Air Corps"}
MILITARY_CALLSIGNS = ("RRR", "ASCOT", "RCH", "NATO", "CNV", "FAF", "GAF", "IAM", "BAF", "SAM")
BIZ_OPERATORS = {"VJT", "NJE", "LXJ", "EJM", "GAJ", "MMD"}

HELI_TEXT = re.compile(r"helicopter|merlin|chinook|apache|puma|lynx|wildcat|sea ?king|gazelle|squirrel|jetranger|"
                       r"black ?hawk|eurocopter|robinson|agusta|sikorsky|\bEC ?\d{3}|\bAW ?\d{3}|\bNH-?90|\bMBB", re.I)
BIZ_CODE = re.compile(r"^(C25.|C5\d\d|C56.|C68.|C700|C750|CL\d\d|GL[0-9A-Z]{2}|GLF\d|G\d{3}|FA\d[A-Z0-9]|F2TH|F900|"
                      r"LJ\d\d|E50P|E55P|PRM1|H25[ABC]|GALX|ASTR|WW24|BE40|FA50|F2EX|PC24|HDJT|SF50|EA50)$")
BIZ_TEXT = re.compile(r"citation|gulfstream|learjet|falcon \d|challenger|global (5|6|7|8|express)|phenom|legacy|"
                      r"praetor|hondajet|hawker|premier|lineage", re.I)
PROP_TYPES = {"C172", "C182", "C152", "C150", "C206", "C210", "P28A", "P28R", "PA28", "PA32", "PA34", "PA44", "PA46",
              "SR20", "SR22", "DA40", "DA42", "DA62", "TB20", "TB10", "BE20", "BE9L", "BE35", "BE36", "BE58", "M20P",
              "AA5", "RV7", "DR40", "PC12", "TBM7", "TBM8", "TBM9", "SW4"}


def is_military(ac) -> bool:
    cs = ac.callsign or ""
    return (bool(ac.military) or any(cs.startswith(p) for p in MILITARY_CALLSIGNS)
            or role_label(ac.owner) in MILITARY_ROLES or ac.type_code in MILITARY_TYPES)


def classify(ac) -> str:
    """glider | balloon | airship | drone | vehicle | helicopter | fighter | bizjet | prop | airliner"""
    t, cat, txt = (ac.type_code or "").upper(), ac.category or "", ac.type_full or ""
    low = txt.lower()
    if t in GLIDER_TYPES or cat in ("B1", "B4"):              # type before category: gliders often say "A1"
        return "glider"
    if cat == "B2" or t in BALLOON_TYPES or re.search(r"balloon|airship|zeppelin|blimp", low):
        if re.search(r"airship|zeppelin|blimp", low) or (ac.gs_kt or 0) > 40:  # balloons just drift; airships have engines
            return "airship"
        return "balloon"
    if cat == "B6" or re.search(r"\b(drone|uav)\b", low):
        return "drone"
    if cat in ("C1", "C2", "C3"):
        return "vehicle"
    if cat == "A7" or t in HELI_TYPES or HELI_TEXT.search(txt):
        return "helicopter"
    if t in FIGHTER_TYPES or (cat == "A6" and is_military(ac)):
        return "fighter"
    if t in {"A400"} or t in MILITARY_TYPES - FIGHTER_TYPES:
        return "airliner"  # military transports and tankers use the airliner shape, in green
    if BIZ_CODE.match(t) or BIZ_TEXT.search(txt) or (ac.callsign or "")[:3] in BIZ_OPERATORS:
        return "bizjet"
    if cat == "A1" or t in PROP_TYPES:
        return "prop"
    return "airliner"


KIND_LABELS = {"glider": "Glider", "balloon": "Balloon", "airship": "Airship", "drone": "Drone", "vehicle": "Vehicle",
               "helicopter": "Helicopter", "fighter": "Fighter jet", "bizjet": "Private jet", "prop": "Light aircraft"}
