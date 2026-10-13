"""Night-mode brightness schedule."""
from __future__ import annotations

import datetime as dt


def _parse(hhmm: str) -> dt.time:
    h, m = hhmm.split(":")
    return dt.time(int(h), int(m))


def effective_brightness(cfg: dict, now: dt.datetime) -> int:
    """Brightness 0-100. 0 means off. Handles windows that cross midnight (23:00 -> 07:00)."""
    day = int(cfg.get("brightness", 70))
    night = cfg.get("night") or {}
    if not night.get("enabled"):
        return day
    try:
        start, end, t = _parse(night["start"]), _parse(night["end"]), now.time()
    except (KeyError, ValueError):
        return day
    if start == end:
        return day
    in_night = (start <= t < end) if start < end else (t >= start or t < end)
    return int(night.get("brightness", 10)) if in_night else day
