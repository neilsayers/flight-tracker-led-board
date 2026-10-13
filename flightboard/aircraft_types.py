from __future__ import annotations

import re
"""ICAO type designator -> friendly name. Unknown codes are shown as-is."""

TYPES = {
    # Airbus
    "A318": "A318", "A319": "A319", "A320": "A320", "A321": "A321",
    "A19N": "A319neo", "A20N": "A320neo", "A21N": "A321neo",
    "A306": "A300-600", "A310": "A310", "A332": "A330-200", "A333": "A330-300",
    "A338": "A330-800", "A339": "A330-900", "A342": "A340-200", "A343": "A340-300",
    "A345": "A340-500", "A346": "A340-600", "A359": "A350-900", "A35K": "A350-1000",
    "A388": "A380", "BCS1": "A220-100", "BCS3": "A220-300",
    # Boeing
    "B712": "717-200", "B732": "737-200", "B733": "737-300", "B734": "737-400",
    "B735": "737-500", "B736": "737-600", "B737": "737-700", "B738": "737-800",
    "B739": "737-900", "B37M": "737 MAX 7", "B38M": "737 MAX 8", "B39M": "737 MAX 9",
    "B3XM": "737 MAX 10", "B744": "747-400", "B748": "747-8", "B752": "757-200",
    "B753": "757-300", "B762": "767-200", "B763": "767-300", "B764": "767-400",
    "B772": "777-200", "B77L": "777-200LR", "B77W": "777-300ER", "B773": "777-300",
    "B778": "777-8", "B779": "777-9", "B788": "787-8", "B789": "787-9", "B78X": "787-10",
    # Regional / other
    "E170": "E170", "E75L": "E175", "E75S": "E175", "E190": "E190", "E195": "E195",
    "E290": "E190-E2", "E295": "E195-E2", "CRJ2": "CRJ-200", "CRJ7": "CRJ-700",
    "CRJ9": "CRJ-900", "CRJX": "CRJ-1000", "DH8D": "Dash 8 Q400", "DH8C": "Dash 8-300",
    "AT72": "ATR 72", "AT76": "ATR 72-600", "AT75": "ATR 72-500", "AT45": "ATR 42",
    "SF34": "Saab 340", "F100": "Fokker 100", "RJ85": "Avro RJ85", "B461": "BAe 146-100",
    "B462": "BAe 146-200", "B463": "BAe 146-300", "SU95": "Sukhoi Superjet 100",
    "C919": "COMAC C919", "AN12": "Antonov An-12", "A124": "Antonov An-124", "A225": "Antonov An-225",
    "C130": "Lockheed C-130", "C17": "Boeing C-17", "A400": "A400M", "H25B": "Hawker 800",
    "C172": "Cessna 172", "C525": "Cessna CitationJet", "C56X": "Cessna Citation XLS",
    "GLF5": "Gulfstream V", "GLF6": "Gulfstream G650", "E55P": "Phenom 300",
    "PC12": "Pilatus PC-12", "CL30": "Bombardier Challenger 300", "CL35": "Bombardier Challenger 350",
    "CL60": "Bombardier Challenger 600", "GLEX": "Bombardier Global Express", "GL5T": "Bombardier Global 5000",
    "GL6T": "Bombardier Global 6000", "GL7T": "Bombardier Global 7500", "C25C": "Cessna Citation CJ4",
    "C25A": "Cessna Citation CJ2", "C25B": "Cessna Citation CJ3", "C680": "Cessna Citation Sovereign",
    "C750": "Cessna Citation X", "FA7X": "Dassault Falcon 7X", "FA8X": "Dassault Falcon 8X",
    "F900": "Dassault Falcon 900", "F2TH": "Dassault Falcon 2000", "LJ45": "Learjet 45", "E50P": "Embraer Phenom 100",
    "PRM1": "Beechcraft Premier I", "GALX": "Gulfstream G200", "GLF4": "Gulfstream IV", "G280": "Gulfstream G280", "EC35": "Eurocopter EC135", "A139": "Leonardo AW139", "R44": "Robinson R44",
}


_MAKERS = [
    (re.compile(r"^(A3\d\d|A3\dK|A19N|A20N|A21N|A400|BCS\d)$"), "Airbus"),
    (re.compile(r"^(B7[0-9A-Z]{2}|B3[0-9X]M)$"), "Boeing"),
    (re.compile(r"^(E1\d\d|E2\d\d|E75[LS]|E55P)$"), "Embraer"),
    (re.compile(r"^CRJ"), "Bombardier"),
    (re.compile(r"^DH8"), "De Havilland"),
]


# Callsign prefix -> operator, for flights the route database doesn't know (business jets, cargo, regional)
OPERATORS = {
    "VJT": "VistaJet", "NJE": "NetJets Europe", "LXJ": "Flexjet", "EJM": "NetJets", "TVF": "Transavia France",
    "CFE": "BA CityFlyer", "SHT": "BA Shuttle", "ASL": "ASL Airlines", "FDX": "FedEx", "UPS": "UPS",
    "DHK": "DHL", "BCS": "European Air Transport", "TAY": "TNT Airways", "CLX": "Cargolux", "GEC": "Lufthansa Cargo",
    "MMD": "Air Alsie", "GAJ": "Gama Aviation", "LCY": "London City", "RRR": "Royal Air Force",
}


# Registered owner -> plain-English role, for aircraft with no airline (helicopters, state and rescue flights)
_ROLES = [
    (r"\b(police|psni|constabulary)\b", "Police"),
    (r"\bcoast ?guard\b", "Coastguard"),
    (r"\b(air ambulance|ambulance|helimed|hems|nhs)\b", "Air Ambulance"),
    (r"\b(fire|rescue service)\b", "Fire & Rescue"),
    (r"\b(royal navy|fleet air arm)\b", "Royal Navy"),
    (r"\b(army air corps|british army)\b", "British Army"),
    (r"\b(royal air force|raf|ministry of defence)\b", "Royal Air Force"),
    (r"\bair corps\b", "Irish Air Corps"),
    (r"\b(border force|customs)\b", "Border Force"),
]
_ROLES = [(re.compile(pat, re.I), label) for pat, label in _ROLES]

# Common helicopter type codes (the ADS-B "category" field A7 catches the rest)
HELI_TYPES = {
    "EC20", "EC25", "EC30", "EC35", "EC45", "EC55", "EC75", "A109", "A119", "A139", "A169", "A189", "AS32", "AS35",
    "AS50", "AS55", "AS65", "B06", "B06T", "B105", "B212", "B407", "B412", "B429", "B505", "R22", "R44", "R66",
    "H500", "MD52", "S76", "S92", "S61", "H60", "EN28", "EN48", "GAZL", "LYNX", "AW09", "H125", "H135", "H145",
    "H160", "H175", "NH90", "PUMA", "MI8", "KA32", "CH47", "H47", "AH64", "H64", "AS3B", "B47G", "SCOUT", "WAH6",
    "EH10", "EH101", "MRLN", "SA33", "SA34", "SA36", "SA31", "S61N", "S58", "BK17", "B427", "B430", "A107", "AS55", "R66",
}


def role_label(owner: str) -> str:
    """'National Police Air Service' -> 'Police'. '' if the owner isn't a recognisable service."""
    for pat, label in _ROLES:
        if owner and pat.search(owner):
            return label
    return ""


def friendly(code: str | None) -> str:
    """'A388' -> 'Airbus A380', 'B738' -> 'Boeing 737-800'. Unknown codes come back as-is."""
    if not code:
        return ""
    code = code.strip().upper()
    model = TYPES.get(code)
    if model is None:
        return code
    for pat, maker in _MAKERS:
        if pat.match(code):
            return model if model.startswith(maker) else f"{maker} {model}"
    return model
