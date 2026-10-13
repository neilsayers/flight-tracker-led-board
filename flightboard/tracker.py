"""Follows one flight number from before departure to landing, using free data only.

Progress is distance flown along the origin -> destination great circle. "Departed" is the time the
board first saw the aircraft airborne near its origin (there is no schedule data), and is left blank
if tracking started mid-flight.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from . import data
from .data import Aircraft, TrackInfo, haversine_nm

LANDED_HOLD = 20 * 60        # seconds to keep showing "Landed" before returning to normal
NEVER_SEEN_EXPIRY = 36 * 3600
ROUTE_RETRY = 300
DROPOUT_GRACE = 120          # a short gap in coverage is not "signal lost"
LANDING_GAP = 180            # vanishing within 40 nm of the destination for this long = landed
NEAR_START = 0.08            # only call it "departed" if first seen within 8% of the route


class Tracker:
    def __init__(self, store: Optional[Path] = None):
        self.store = Path(store) if store else None
        self.token = None
        self.ac_lookup = data.AircraftLookup()
        self._reset(None)

    def _reset(self, token):
        self.token, self.route, self.next_route_try = token, None, 0.0
        self.seen_airborne, self.departed_at, self.landed_at = False, None, None
        self.last_seen, self.last_ac, self.last_remaining, self.last_phase = None, None, None, "cruising"
        if token and self.store:
            try:
                d = json.loads(self.store.read_text())
                if d.get("token") == token:
                    self.departed_at = _dt(d.get("departed_at"))
                    self.landed_at = _dt(d.get("landed_at"))
                    self.seen_airborne = bool(d.get("seen_airborne"))
            except (OSError, ValueError):
                pass

    def _save(self):
        if not self.store:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self.store.parent, prefix=".track-", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump({"token": self.token, "seen_airborne": self.seen_airborne,
                           "departed_at": self.departed_at.isoformat() if self.departed_at else None,
                           "landed_at": self.landed_at.isoformat() if self.landed_at else None}, f)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.store)
        except OSError:
            pass

    def _base(self, flight: str) -> Aircraft:
        ac = Aircraft(hex="trk-" + flight.lower(), callsign=(self.route or {}).get("callsign_icao") or flight)
        for f in data.ROUTE_FIELDS:
            if self.route and f in self.route:
                setattr(ac, f, self.route[f])
        return ac

    def step(self, flight: str, started: Optional[float], now: dt.datetime):
        """-> (aircraft, TrackInfo, ok). TrackInfo None means tracking is over (expired). ok False = data problem."""
        token = f"{flight}:{started}"
        if token != self.token:
            self._reset(token)
        ts = now.timestamp()
        if self.landed_at and (now - self.landed_at).total_seconds() > LANDED_HOLD:
            return None, None, True
        if not self.seen_airborne and started and ts - started > NEVER_SEEN_EXPIRY:
            return None, None, True
        if self.route is None and ts >= self.next_route_try:
            self.route, self.next_route_try = data.lookup_flight(flight), ts + ROUTE_RETRY
        base = self._base(flight)
        info = TrackInfo(flight=flight, phase="waiting")
        if self.route is None:
            return base, info, False

        icao = base.callsign
        found = data.fetch_callsign(icao)
        ok = found is not None
        live = None
        for a in found or []:
            if live is None or (live.on_ground and not a.on_ground):
                live = a

        if self.landed_at:  # already landed: just keep showing it
            return self._landed_view(base, info), self._landed_info(info), ok

        o = (self.route["origin_lat"], self.route["origin_lon"])
        d = (self.route["dest_lat"], self.route["dest_lon"])
        if live is not None:
            data.copy_route(base, live)
            self.ac_lookup.enrich(live)
            self.last_ac, self.last_seen = live, now
            from_o, to_d = haversine_nm(*o, live.lat, live.lon), haversine_nm(live.lat, live.lon, *d)
            self.last_remaining = to_d
            progress = from_o / (from_o + to_d) if (from_o + to_d) else None
            if live.on_ground:
                if self.seen_airborne and to_d < 25:
                    return self._land(now, base, info)
                return live, info, ok  # still on the stand / taxiing before departure
            if not self.seen_airborne:
                self.seen_airborne = True
                if progress is not None and progress <= NEAR_START:
                    self.departed_at = now
                self._save()
            info.progress, info.departed_at, info.last_seen = progress, self.departed_at, now
            info.remaining_nm = to_d
            info.phase = self._phase(live, progress)
            self.last_phase = info.phase
            if live.gs_kt and live.gs_kt >= 100:
                info.eta = now + dt.timedelta(hours=to_d / live.gs_kt)
            return live, info, ok

        if not self.seen_airborne:
            return base, info, ok  # nothing yet: waiting for departure
        gap = (now - self.last_seen).total_seconds() if self.last_seen else 0
        if self.last_remaining is not None and self.last_remaining < 40 and gap > LANDING_GAP and ok:
            return self._land(now, base, info, when=self.last_seen)
        la = self.last_ac or base
        total = haversine_nm(*o, *d)
        info.progress = max(0.0, min(1.0, 1 - (self.last_remaining or 0) / total)) if total else None
        info.departed_at, info.last_seen = self.departed_at, self.last_seen
        info.remaining_nm = self.last_remaining
        info.phase = "lost" if gap > DROPOUT_GRACE else self.last_phase
        return la, info, ok

    @staticmethod
    def _phase(ac: Aircraft, progress) -> str:
        vr, alt = ac.vrate_fpm or 0, ac.alt_ft or 0
        if progress is not None and progress < 0.15 and alt < 10000:
            return "climbing"
        if progress is not None and progress > 0.85 and alt < 10000:
            return "descending"
        return "climbing" if vr > 500 else "descending" if vr < -500 else "cruising"

    def _land(self, now, base, info, when=None):
        self.landed_at = when or now
        self._save()
        return self._landed_view(base, info), self._landed_info(info), True

    def _landed_view(self, base, info) -> Aircraft:
        ac = self.last_ac or base
        ac.on_ground, ac.alt_ft, ac.gs_kt, ac.vrate_fpm = True, 0.0, 0.0, None
        return ac

    def _landed_info(self, info) -> TrackInfo:
        info.phase, info.progress = "landed", 1.0
        info.departed_at, info.landed_at = self.departed_at, self.landed_at
        return info


def _dt(v):
    try:
        return dt.datetime.fromisoformat(v) if v else None
    except ValueError:
        return None
