"""Fetching: nearest aircraft from adsb.lol / airplanes.live, route+airline from adsbdb.

No API keys. Everything here fails soft: network errors return None / empty and the
caller decides what to show.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from .aircraft_types import OPERATORS, friendly, role_label
from .kinds import classify, is_military

USER_AGENT = "flight-board/1.0 (personal LED display)"
SOURCES = [
    "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius}",
    "https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{radius}",
    "https://api.airplanes.live/v2/point/{lat}/{lon}/{radius}",  # often blocks scripts; last resort
]
CALLSIGN_SOURCES = [
    "https://api.adsb.lol/v2/callsign/{callsign}",
    "https://opendata.adsb.fi/api/v2/callsign/{callsign}",
]
AIRCRAFT_URL = "https://api.adsbdb.com/v0/aircraft/{hex}"
MAX_POS_AGE = 30  # seconds: ignore aircraft whose last position is older than this
ROUTE_URL = "https://api.adsbdb.com/v0/callsign/{callsign}"
POSTCODE_URL = "https://api.postcodes.io/postcodes/{postcode}"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"  # OpenStreetMap data; policy: identify the app, <=1 request/second
UK_POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.I)
HTTP_TIMEOUT = 8
ROUTE_TTL = 3600  # callsigns get reused for different destinations, so keep a looked-up route for an hour at most
ROUTE_MISS_TTL = 10 * 60


@dataclass
class Aircraft:
    hex: str
    callsign: str = ""
    registration: str = ""
    type_code: str = ""
    type_full: str = ""      # readable name from adsbdb, e.g. "Cessna Citation CJ4"
    owner: str = ""          # registered owner, e.g. "National Police Air Service"
    seen_pos: Optional[float] = None  # seconds since the position was last updated
    category: str = ""       # ADS-B emitter category, e.g. "A3" airliner, "A7" helicopter
    military: bool = False
    lat: float = 0.0
    lon: float = 0.0
    alt_ft: Optional[float] = None  # None = unknown
    on_ground: bool = False
    gs_kt: Optional[float] = None
    track: Optional[float] = None
    vrate_fpm: Optional[float] = None
    distance_nm: float = 0.0
    # filled in from adsbdb
    airline: str = ""
    airline_icao: str = ""
    origin: str = ""
    destination: str = ""
    origin_name: str = ""
    destination_name: str = ""
    dest_lat: Optional[float] = None
    dest_lon: Optional[float] = None
    origin_lat: Optional[float] = None
    origin_lon: Optional[float] = None

    @property
    def type_name(self) -> str:
        """Curated name for common airliners; otherwise the full name from adsbdb; otherwise the raw code."""
        name = friendly(self.type_code)
        if name == self.type_code and self.type_full:
            return self.type_full
        return name

    @property
    def route(self) -> str:
        if self.origin and self.destination:
            return f"{self.origin}-{self.destination}"
        return ""

    @property
    def operator(self) -> str:
        """Best name for who flies it: airline from the route database, else known operator by callsign, else owner."""
        op = self.airline or OPERATORS.get(self.airline_prefix, "") or role_label(self.owner)
        if not op and self.is_military:
            op = "Military"
        return op or self.owner

    @property
    def kind(self) -> str:
        """glider | balloon | airship | drone | vehicle | helicopter | fighter | bizjet | prop | airliner"""
        return classify(self)

    @property
    def is_military(self) -> bool:
        return is_military(self)

    @property
    def is_helicopter(self) -> bool:
        return self.kind == "helicopter"

    @property
    def airline_prefix(self) -> str:
        """Three-letter ICAO airline code from the callsign (UAL123 -> UAL)."""
        m = re.match(r"^([A-Z]{3})\d", self.callsign)
        return m.group(1) if m else ""


@dataclass
class TrackInfo:
    """State of one followed flight. phase: waiting | climbing | cruising | descending | landed."""
    flight: str = ""                       # what the user typed, e.g. BA117
    phase: str = "waiting"         # waiting | climbing | cruising | descending | landed | lost
    progress: Optional[float] = None       # 0..1 along the great circle origin -> destination
    departed_at: Optional["dt.datetime"] = None   # first seen airborne near the origin: there is no schedule data
    last_seen: Optional["dt.datetime"] = None
    landed_at: Optional["dt.datetime"] = None
    eta: Optional["dt.datetime"] = None
    remaining_nm: Optional[float] = None


def haversine_nm(lat1, lon1, lat2, lon2) -> float:
    r = 3440.065  # earth radius in nautical miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_aircraft(raw: dict, lat: float, lon: float) -> Optional[Aircraft]:
    """One entry of the API's `ac` list -> Aircraft, or None if it has no position."""
    la, lo = _num(raw.get("lat")), _num(raw.get("lon"))
    if la is None or lo is None or not raw.get("hex"):
        return None
    alt = raw.get("alt_baro")
    on_ground = alt == "ground"
    return Aircraft(
        hex=str(raw["hex"]).lower(),
        callsign=str(raw.get("flight") or "").strip().upper(),
        registration=str(raw.get("r") or "").strip(),
        type_code=str(raw.get("t") or "").strip().upper(),
        lat=la,
        lon=lo,
        alt_ft=0.0 if on_ground else _num(alt),
        on_ground=on_ground,
        gs_kt=_num(raw.get("gs")),
        track=_num(raw.get("track")),
        vrate_fpm=_num(raw.get("baro_rate")),
        distance_nm=haversine_nm(lat, lon, la, lo),
        seen_pos=_num(raw.get("seen_pos")),
        category=str(raw.get("category") or ""),
        military=isinstance(raw.get("dbFlags"), int) and bool(raw["dbFlags"] & 1),
    )


def pick_nearest(aircraft: list[Aircraft], include_ground: bool = False) -> Optional[Aircraft]:
    pool = [a for a in aircraft if include_ground or not a.on_ground]
    return min(pool, key=lambda a: a.distance_nm) if pool else None


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.load(resp)


def _aircraft_list(payload):
    """adsb.lol and airplanes.live call the list 'ac'; adsb.fi calls it 'aircraft'."""
    for key in ("ac", "aircraft"):
        if isinstance(payload.get(key), list):
            return payload[key]
    return None


def fetch_nearby(lat: float, lon: float, radius_nm: float) -> Optional[list[Aircraft]]:
    """All aircraft within radius, or None if every source failed (distinct from [] = none overhead)."""
    radius = max(1, min(250, int(round(radius_nm))))
    for tmpl in SOURCES:
        try:
            data = _get_json(tmpl.format(lat=f"{lat:.4f}", lon=f"{lon:.4f}", radius=radius))
            ac = _aircraft_list(data)
            if ac is None:
                continue
            parsed = (parse_aircraft(r, lat, lon) for r in ac if isinstance(r, dict))
            return [a for a in parsed if a is not None and (a.seen_pos is None or a.seen_pos <= MAX_POS_AGE)]
        except (urllib.error.URLError, OSError, ValueError, AttributeError):
            continue
    return None


ROUTE_FIELDS = ("airline", "airline_icao", "origin", "destination", "origin_name", "destination_name",
                "dest_lat", "dest_lon", "origin_lat", "origin_lon")


def copy_route(src: "Aircraft", dst: "Aircraft") -> None:
    for f in ROUTE_FIELDS + ("type_full", "owner"):
        setattr(dst, f, getattr(src, f))


def airport_name(ap: dict) -> str:
    """adsbdb airport -> short display name: 'London Heathrow', 'Paris Charles de Gaulle', 'Dublin'."""
    name = (ap.get("name") or "").strip()
    muni = (ap.get("municipality") or "").strip()
    short = re.sub(r"\s+Airport$", "", name, flags=re.I)
    no_intl = re.sub(r"\s+International$", "", short, flags=re.I)
    if no_intl and no_intl.lower() != muni.lower():  # 'Belfast International' must not collapse to 'Belfast'
        short = no_intl
    if muni and muni.lower() not in short.lower():
        short = f"{muni} {short}"
    return short or ap.get("iata_code") or ap.get("icao_code") or ""


class RouteLookup:
    """adsbdb callsign -> (airline, origin, destination), cached in memory."""

    def __init__(self):
        self._cache: dict[str, tuple[float, dict]] = {}

    def enrich(self, ac: Aircraft) -> Aircraft:
        if not ac.callsign:
            return ac
        info = self._lookup(ac.callsign)
        ac.airline = info.get("airline", "")
        ac.airline_icao = info.get("airline_icao", "")
        for f in ROUTE_FIELDS:
            setattr(ac, f, info.get(f, getattr(ac, f)))
        return ac

    def _lookup(self, callsign: str) -> dict:
        now = time.time()
        hit = self._cache.get(callsign)
        if hit and hit[0] > now:
            return hit[1]
        try:
            data = _get_json(ROUTE_URL.format(callsign=urllib.parse.quote(callsign)))
            info = parse_route(data)
            ttl = ROUTE_TTL if info else ROUTE_MISS_TTL
        except urllib.error.HTTPError as e:
            info, ttl = {}, (ROUTE_MISS_TTL if e.code == 404 else 60)  # 404 = unknown callsign
        except (urllib.error.URLError, OSError, ValueError):
            info, ttl = {}, 30  # transient: retry soon, but not every poll
        self._cache[callsign] = (now + ttl, info)
        if len(self._cache) > 500:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        return info


def parse_route(data) -> dict:
    try:
        fr = data["response"]["flightroute"]
    except (KeyError, TypeError):
        return {}
    if not isinstance(fr, dict):
        return {}

    def code(ap):
        ap = ap or {}
        return ap.get("iata_code") or ap.get("icao_code") or ""

    airline = fr.get("airline") or {}
    dest = fr.get("destination") or {}
    orig = fr.get("origin") or {}
    return {
        "callsign_icao": fr.get("callsign_icao") or "",
        "origin_lat": _num(orig.get("latitude")),
        "origin_lon": _num(orig.get("longitude")),
        "origin_name": airport_name(fr.get("origin") or {}),
        "destination_name": airport_name(dest),
        "dest_lat": _num(dest.get("latitude")),
        "dest_lon": _num(dest.get("longitude")),
        "airline": airline.get("name", ""),
        "airline_icao": airline.get("icao", ""),
        "origin": code(fr.get("origin")),
        "destination": code(fr.get("destination")),
    }


_KEEP_UPPER = {"LLC", "LTD", "PLC", "UK", "USA", "GV", "NPAS", "BV", "AG", "SA", "AB", "AS", "GMBH", "KG", "II", "III"}


def _nice_owner(name: str) -> str:
    """'WESTGATE AVIATION GV LLC' -> 'Westgate Aviation GV LLC'. Mixed-case names are left alone."""
    if not name or not name.isupper():
        return name
    return " ".join(w if w in _KEEP_UPPER else w.capitalize() for w in name.split())


def parse_aircraft_info(payload) -> dict:
    """adsbdb /aircraft/{hex} -> readable type name, owner, registration. {} if unknown."""
    try:
        a = payload["response"]["aircraft"]
    except (KeyError, TypeError):
        return {}
    if not isinstance(a, dict):
        return {}
    maker, typ = (a.get("manufacturer") or "").strip(), (a.get("type") or "").strip()
    if maker.isupper():
        maker = maker.title()  # "CESSNA" -> "Cessna"
    full = typ if (not maker or typ.lower().startswith(maker.lower())) else f"{maker} {typ}"
    return {"type_full": full, "owner": _nice_owner((a.get("registered_owner") or "").strip()),
            "registration": (a.get("registration") or "").strip()}


class AircraftLookup:
    """adsbdb hex -> readable type and owner, cached (aircraft don't change)."""

    def __init__(self):
        self._cache: dict[str, tuple[float, dict]] = {}

    def enrich(self, ac: Aircraft) -> Aircraft:
        info = self._lookup(ac.hex)
        ac.type_full = info.get("type_full", "")
        ac.owner = info.get("owner", "")
        if not ac.registration and info.get("registration"):
            ac.registration = info["registration"]
        return ac

    def _lookup(self, hex_: str) -> dict:
        now = time.time()
        hit = self._cache.get(hex_)
        if hit and hit[0] > now:
            return hit[1]
        try:
            info = parse_aircraft_info(_get_json(AIRCRAFT_URL.format(hex=urllib.parse.quote(hex_))))
            ttl = 24 * 3600 if info else 3600
        except urllib.error.HTTPError as e:
            info, ttl = {}, (3600 if e.code == 404 else 60)
        except (urllib.error.URLError, OSError, ValueError):
            info, ttl = {}, 30
        self._cache[hex_] = (now + ttl, info)
        if len(self._cache) > 500:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        return info


def lookup_flight(flight: str) -> Optional[dict]:
    """Flight number or callsign ('BA11', 'BAW11') -> route info with both airports' coordinates, or None."""
    try:
        info = parse_route(_get_json(ROUTE_URL.format(callsign=urllib.parse.quote(flight))))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not info or info.get("origin_lat") is None or info.get("dest_lat") is None:
        return None
    return info


def fetch_callsign(callsign: str) -> Optional[list[Aircraft]]:
    """Live aircraft broadcasting this callsign anywhere in range. [] = not airborne/visible, None = every source failed."""
    for tmpl in CALLSIGN_SOURCES:
        try:
            ac = _aircraft_list(_get_json(tmpl.format(callsign=urllib.parse.quote(callsign))))
            if ac is None:
                continue
            return [a for a in (parse_aircraft(r, 0.0, 0.0) for r in ac if isinstance(r, dict)) if a]
        except (urllib.error.URLError, OSError, ValueError, AttributeError):
            continue
    return None


def search_places(query: str, limit: int = 5) -> list[dict]:
    """Free-text place search on OpenStreetMap (UK and Ireland). [{'name', 'lat', 'lon'}], best match first."""
    qs = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": limit, "countrycodes": "gb,ie"})
    try:
        rows = _get_json(NOMINATIM_URL + "?" + qs)
        out = []
        for r in rows:
            parts = [p.strip() for p in str(r.get("display_name", "")).split(",")]
            out.append({"name": ", ".join(parts[:3]), "lat": float(r["lat"]), "lon": float(r["lon"])})
        return out
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return []


def geocode(query: str) -> Optional[dict]:
    """A UK postcode (exact, via postcodes.io) or any place name ('Omagh', 'Clevedon, Somerset')."""
    q = (query or "").strip()
    if not q:
        return None
    if UK_POSTCODE_RE.match(q):
        pos = lookup_postcode(q)
        if pos:
            pc = re.sub(r"\s+", "", q).upper()
            return {"name": pc[:-3] + " " + pc[-3:], "lat": pos[0], "lon": pos[1]}
    hits = search_places(q, limit=1)
    return hits[0] if hits else None


def lookup_postcode(postcode: str) -> Optional[tuple[float, float]]:
    """UK postcode -> (lat, lon) via postcodes.io. None if invalid or unreachable."""
    pc = re.sub(r"\s+", "", postcode or "").upper()
    if not re.fullmatch(r"[A-Z0-9]{5,8}", pc):
        return None
    try:
        res = _get_json(POSTCODE_URL.format(postcode=pc)).get("result") or {}
        return float(res["latitude"]), float(res["longitude"])
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return None
