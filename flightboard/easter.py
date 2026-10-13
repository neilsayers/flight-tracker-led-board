"""Personal touches: rare-aircraft alerts, dated messages, and the spotted-aircraft counter."""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from .data import Aircraft

RARE_TYPES = {
    "A388": "Airbus A380", "B748": "Boeing 747-8", "B744": "Boeing 747-400", "A124": "Antonov An-124",
    "A225": "Antonov An-225", "BLCF": "Boeing Dreamlifter", "A3ST": "Airbus Beluga", "A337": "Beluga XL",
    "A400": "Airbus A400M", "C17": "C-17 Globemaster", "C5M": "C-5 Galaxy", "B52": "B-52 Stratofortress",
    "K35R": "KC-135 tanker", "E3TF": "E-3 Sentry", "A306": "Airbus A300-600", "MD11": "MD-11",
}
RARE_CALLSIGNS = {
    "RRR": "Red Arrows", "RCH": "USAF air mobility", "ASCOT": "RAF transport", "NATO": "NATO flight",
    "CNV": "US Navy", "SAM": "US government flight", "KING": "Rescue flight",
}
MILESTONES = (50, 100, 500, 1000)
SAVE_EVERY = 300   # seconds: the count is written to the SD card at most this often (plus at milestones and shutdown)
DEDUP_SECS = 6 * 3600  # a plane circling overhead is one sighting, not ten


def rarity(ac: Aircraft, cfg: dict) -> Optional[str]:
    """Why this aircraft is special ('Airbus A380', 'Red Arrows'), or None."""
    e = cfg.get("easter") or {}
    if not e.get("rare_alerts", True):
        return None
    watch_t = {t.strip().upper() for t in e.get("watch_types", [])}
    watch_c = [c.strip().upper() for c in e.get("watch_callsigns", []) if c.strip()]
    if ac.type_code in RARE_TYPES or ac.type_code in watch_t:
        return RARE_TYPES.get(ac.type_code) or ac.type_name or ac.type_code
    for prefix in list(RARE_CALLSIGNS) + watch_c:
        if ac.callsign.startswith(prefix):
            return RARE_CALLSIGNS.get(prefix, prefix)
    return None


def easter_sunday(year: int) -> dt.date:
    """Gregorian Easter (Meeus/Jones/Butcher algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def mothers_day(year: int) -> dt.date:
    """UK and Ireland: Mothering Sunday, the fourth Sunday of Lent = three weeks before Easter."""
    return easter_sunday(year) - dt.timedelta(days=21)


def fathers_day(year: int) -> dt.date:
    """UK and Ireland: third Sunday in June."""
    first = dt.date(year, 6, 1)
    return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 14)


def active_event(cfg: dict, today: dt.date) -> Optional[tuple[str, str]]:
    """(kind, text) for today's message: your own dates first, then built-in Christmas."""
    name = (cfg.get("name") or "").strip()
    md = today.strftime("%m-%d")
    for ev in cfg.get("events") or []:
        if ev.get("date") == md and ev.get("text"):
            return ev.get("kind", "custom"), ev["text"]
    tail = f", {name}" if name else ""
    if today == mothers_day(today.year):
        return "mothers", f"Happy Mother's Day{tail}!"
    if today == fathers_day(today.year):
        return "fathers", f"Happy Father's Day{tail}!"
    if today.month == 12 and today.day in (24, 25, 26):
        return "xmas", f"Merry Christmas{', ' + name if name else ''}!"
    if md == "01-01":
        return "custom", f"Happy New Year{', ' + name if name else ''}!"
    return None


def parse_events(text: str) -> list[dict]:
    """Settings-page textarea, one per line: '03-14 Happy Birthday Sam!' -> events."""
    out = []
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            dt.datetime.strptime(parts[0], "%m-%d")
        except ValueError:
            continue
        low = parts[1].lower()
        kind = "xmas" if "christmas" in low or "xmas" in low else "birthday" if "birthday" in low or "bday" in low else "custom"
        out.append({"date": parts[0], "text": parts[1][:80], "kind": kind})
    return out[:20]


def time_greeting(now: dt.datetime, name: str = "") -> str:
    """'Good morning, Sam': morning 05-12, afternoon 12-18, evening 18-22, night 22-05."""
    h = now.hour
    part = "morning" if 5 <= h < 12 else "afternoon" if 12 <= h < 18 else "evening" if h < 22 and h >= 18 else "night"
    return f"Good {part}" + (f", {name.strip()}" if name and name.strip() else "")


def is_milestone(n: int) -> bool:
    return n in MILESTONES or (n > 1000 and n % 1000 == 0)


class SpotLog:
    """Counts distinct aircraft shown on the board. Stored in state.json next to config.json."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.count, self.seen = 0, {}
        self._dirty, self._last_save = False, 0.0
        self.reset_token = None  # which "reset" request from the settings page has already been applied
        try:
            with open(self.path) as f:
                d = json.load(f)
            self.count, self.seen = int(d.get("count", 0)), dict(d.get("seen", {}))
            self.reset_token = d.get("reset")
        except (OSError, ValueError, TypeError):
            pass

    def record(self, hex_: str, now: float) -> tuple[int, bool]:
        """Returns (count, hit_milestone). Same aircraft within DEDUP_SECS is not counted again."""
        if now - self.seen.get(hex_, -DEDUP_SECS - 1) < DEDUP_SECS:
            return self.count, False
        self.seen[hex_] = now
        self.seen = {h: t for h, t in self.seen.items() if now - t < DEDUP_SECS}
        self.count += 1
        self._dirty = True
        hit = is_milestone(self.count)
        if hit or now - self._last_save >= SAVE_EVERY:   # milestones are saved at once so a reboot can't repeat them
            self._save(now)
        return self.count, hit

    def apply_reset(self, token, now: Optional[float] = None) -> bool:
        """The settings page asks for a reset by putting a new token in config.json. Zero the count (and forget which
        aircraft were already counted, so they count again) once per token. True if a reset happened."""
        if not token or token == self.reset_token:
            return False
        self.count, self.seen, self.reset_token = 0, {}, token
        self._save(time.time() if now is None else now)
        return True

    def flush(self, now: Optional[float] = None):
        """Write any unsaved count. Called on a clean shutdown."""
        if self._dirty:
            self._save(time.time() if now is None else now)

    def _save(self, now: float):
        try:
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump({"count": self.count, "seen": self.seen, "reset": self.reset_token}, f)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.path)
            self._dirty, self._last_save = False, now
        except OSError:
            pass  # a read-only disk must not stop the display


def read_count(path: Path) -> int:
    try:
        with open(path) as f:
            return int(json.load(f).get("count", 0))
    except (OSError, ValueError, TypeError):
        return 0
