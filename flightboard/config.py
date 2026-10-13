"""Shared config.json: read by the display, read/written by the settings server."""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

DEFAULT_PATH = Path(os.environ.get("FLIGHTBOARD_CONFIG", Path(__file__).resolve().parent.parent / "config.json"))

DEFAULTS = {
    "location": "",   # what was typed: a town or a postcode
    "place": "",      # what it resolved to, shown on the settings page
    "lat": None,
    "lon": None,
    "radius_nm": 15,
    "poll_secs": 8,
    "mode": "rotate",      # "rotate": cycle through the nearest few aircraft; "nearest": only the closest
    "dwell_secs": 30,      # rotate mode: seconds on each aircraft
    "rotate_count": 6,     # rotate mode: how many of the nearest aircraft to cycle through
    "brightness": 70,  # 1-100
    "units": {"speed": "mph", "altitude": "ft"},  # mph|kt|kmh, ft|m
    "include_ground": False,
    "name": "",        # used in greetings, e.g. "Merry Christmas, Sam!"
    "greeting": "",    # shown on the boot and idle screens, e.g. "Hi Sam!"
    "spotted_reset": None,  # the settings page puts a new value here to ask the display to zero the plane count
    "track": {"flight": "", "started": None},  # set from the settings page
    "events": [],      # [{"date": "03-14", "text": "Happy Birthday Sam!", "kind": "birthday"}]
    "easter": {"rare_alerts": True, "milestones": True, "watch_types": [], "watch_callsigns": []},
    "night": {"enabled": False, "start": "23:00", "end": "07:00", "brightness": 10},  # 0 = off
    "hardware": {  # only used on the Pi
        "hardware_mapping": "adafruit-hat-pwm",  # "adafruit-hat" if the GPIO 4-18 mod is NOT fitted
        "rows": 64,
        "cols": 64,
        "chain_length": 2,
        "gpio_slowdown": 2,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: Path = DEFAULT_PATH) -> dict:
    """Defaults overlaid with the file. A missing or corrupt file gives defaults."""
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    return _merge(DEFAULTS, data)


def save(cfg: dict, path: Path = DEFAULT_PATH) -> None:
    """Atomic write so the display never reads a half-written file."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
        os.chmod(tmp, 0o644)  # the display drops privileges after init and must still read this
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def mtime(path: Path = DEFAULT_PATH) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0
