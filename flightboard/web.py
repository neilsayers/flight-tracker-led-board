"""Settings server: mobile-first page that edits config.json. The display reloads it on change.

  sudo python -m flightboard.web         # on the Pi: port 80 (needs root)
  FLIGHTBOARD_PORT=8080 python -m flightboard.web   # on a Mac
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import threading
import time

from flask import Flask, render_template_string, request

from . import config, data, easter
from .display import local_ip

app = Flask(__name__)
CONFIG_PATH = config.DEFAULT_PATH

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flight Board</title>
<style>
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;margin:0;background:#111;color:#ddd}
.wrap{max-width:520px;margin:0 auto;padding:20px 16px 0}
h1{color:#ffa000;margin:0 0 2px}
.ip{color:#666;font-size:12px;margin:0 0 10px}
.top{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
.card{background:#171717;border:1px solid #2a2a2a;border-radius:8px;padding:2px 18px 20px;margin:0 0 20px;break-inside:avoid}
h2{color:#ffa000;font-size:14px;margin:18px 0 0;text-transform:uppercase;letter-spacing:.07em}
label{display:block;margin-top:16px;font-size:13px;color:#aaa}
input[type=text],input[type=password],input[type=number],input[type=time],select,textarea{width:100%;padding:10px;margin-top:4px;background:#222;color:#eee;border:1px solid #444;border-radius:4px;font-size:16px;font-family:inherit}
input[type=range]{width:100%;margin-top:6px;accent-color:#ffa000}
.row{display:flex;gap:10px}.row>*{flex:1}
.check{display:flex;align-items:center;gap:10px;color:#ddd;font-size:15px}
.check input{width:20px;height:20px;accent-color:#ffa000}
.check label{margin:0;color:#ddd;font-size:15px}
small{font-size:12px;color:#777;display:block;margin-top:4px}
button{margin-top:22px;width:100%;padding:13px;background:#ffa000;color:#000;border:none;border-radius:4px;font-size:16px;font-weight:bold;cursor:pointer}
button:active{background:#cc8000}
.ok{color:#87ff46;margin:0 0 14px;font-size:14px}.err{color:#ff6b6b;margin:0 0 14px;font-size:14px}
.inline{display:flex;flex-wrap:wrap;gap:0 16px;align-items:flex-end}
.inline>label{flex:1 1 220px}
.inline>button,.inline>.note{flex:0 0 auto}
.secondary{background:transparent;color:#ffa000;border:1px solid #ffa000;margin-top:14px;width:auto;padding:9px 18px;font-size:14px}
.secondary:active{background:#2a1c00}
.save-top{display:none;margin:0;width:auto;padding:12px 30px}
.savebar{position:sticky;bottom:0;z-index:5;margin:0 -16px;padding:18px 16px calc(14px + env(safe-area-inset-bottom));
 background:linear-gradient(to top,#111 72%,rgba(17,17,17,0));display:flex;justify-content:flex-end}
.savebar button{margin:0}
@media(min-width:760px){
 .wrap{max-width:1180px;padding:24px 28px 0}
 .grid{column-width:340px;column-gap:20px}
 .save-top{display:block}
 .savebar{margin:0 -28px;padding:18px 28px calc(14px + env(safe-area-inset-bottom))}
 .savebar button{width:auto;min-width:240px;padding:13px 36px}
 .inline button{margin-bottom:0;padding:11px 24px;width:auto}
}
</style></head><body><div class="wrap">
<div class="top">
 <div><h1>Flight Board</h1>
 <p class="ip">http://{{ host }}.local &middot; {{ ip or 'no network' }}</p></div>
 <button type="submit" form="mainform" class="save-top">Save &amp; apply</button>
</div>
{% if ok %}<p class="ok">&#10003; {{ ok }}</p>{% endif %}
{% if err %}<p class="err">{{ err }}</p>{% endif %}

<form method="post" action="/track" class="card">
<h2>Track a flight</h2>
{% if c.track.flight %}
<div class="inline">
<p class="note" style="margin:14px 0 0;color:#ddd;flex:1 1 260px">Following <b style="color:#ffa000">{{ c.track.flight }}</b>. The board shows only this flight until about 20 minutes after it lands.</p>
<button name="stop" value="1">Stop tracking</button>
</div>
{% else %}
<div class="inline">
<label>Flight number
<input type="text" name="flight" placeholder="e.g. BA11 or EZY8271" autocapitalize="characters" autocomplete="off" maxlength="9"></label>
<button>Start tracking</button>
</div>
<small>Airline code and number. Works for a flight that hasn't left yet.</small>
{% endif %}
</form>

<form method="post" action="/" id="mainform" class="grid">
<section class="card">
<h2>Location</h2>
<label>Town, place or postcode
<input type="text" name="location" value="{{ c.location }}" placeholder="e.g. Omagh, or BA21 4AA" autocomplete="off"></label>
<small>{% if c.lat is not none %}Using <b>{{ c.place or c.location }}</b> ({{ '%.4f'|format(c.lat) }}, {{ '%.4f'|format(c.lon) }}). Wrong place? Add the county, like "Clevedon, Somerset".{% else %}Not set yet. The board shows a prompt until you save a location.{% endif %}</small>
<label>Radius (nautical miles)
<input type="number" name="radius_nm" min="1" max="100" value="{{ c.radius_nm }}"></label>
<small>1 nm is about 1.15 miles. 10-20 is a good start.</small>
<label>What to show
<select name="mode"><option value="rotate" {{ 'selected' if c.mode=='rotate' }}>Rotate through nearby aircraft</option><option value="nearest" {{ 'selected' if c.mode=='nearest' }}>Only the nearest aircraft</option></select></label>
<label>How often to change aircraft
<select name="dwell_secs">
<option value="10" {{ 'selected' if c.dwell_secs==10 }}>Fast: every 10 seconds</option>
<option value="20" {{ 'selected' if c.dwell_secs==20 }}>Medium: every 20 seconds</option>
<option value="30" {{ 'selected' if c.dwell_secs==30 }}>Slow: every 30 seconds</option>
{% if c.dwell_secs not in (10, 20, 30) %}<option value="{{ c.dwell_secs }}" selected>Custom: every {{ c.dwell_secs }} seconds</option>{% endif %}
</select></label>
<small>Rotate mode cycles through up to {{ c.rotate_count }} of the nearest aircraft. Fast suits a busy sky like London; slow suits somewhere quieter. Nearest-only stays on the closest plane, which can be the same one for a long time.</small>
<div class="check" style="margin-top:16px"><input type="checkbox" name="include_ground" id="ig" {{ 'checked' if c.include_ground }}><label for="ig">Include aircraft on the ground</label></div>
</section>

<section class="card">
<h2>Display</h2>
<label id="bl">Brightness ({{ c.brightness }}%)
<input type="range" name="brightness" min="1" max="100" value="{{ c.brightness }}"
 oninput="document.getElementById('bl').firstChild.textContent='Brightness ('+this.value+'%)'"></label>
<div class="row">
<label>Speed<select name="speed">{% for u in ['mph','kt','kmh'] %}<option {{ 'selected' if c.units.speed==u }}>{{ u }}</option>{% endfor %}</select></label>
<label>Altitude<select name="altitude">{% for u in ['ft','m'] %}<option {{ 'selected' if c.units.altitude==u }}>{{ u }}</option>{% endfor %}</select></label>
</div>
</section>

<section class="card">
<h2>Night mode</h2>
<div class="check" style="margin-top:14px"><input type="checkbox" name="night_enabled" id="ne" {{ 'checked' if c.night.enabled }}><label for="ne">Dim at night</label></div>
<div class="row">
<label>From<input type="time" name="night_start" value="{{ c.night.start }}"></label>
<label>Until<input type="time" name="night_end" value="{{ c.night.end }}"></label>
</div>
<label>Night brightness ({{ c.night.brightness }}%)
<input type="range" name="night_brightness" min="0" max="100" value="{{ c.night.brightness }}"
 oninput="this.parentNode.firstChild.textContent='Night brightness ('+this.value+'%)'"></label>
<small>0 turns the display off during that window.</small>
</section>

<section class="card">
<h2>Personal touches</h2>
<label>Name<input type="text" name="name" value="{{ c.name }}" maxlength="20" autocomplete="off"></label>
<label>Greeting<input type="text" name="greeting" value="{{ c.greeting }}" maxlength="40" placeholder="e.g. Hi Sam!" autocomplete="off"></label>
<small>Shown when the board starts and now and then on the clock screen.</small>
<label>Special dates
<textarea name="events" rows="3" placeholder="03-14 Happy Birthday Sam!&#10;06-02 Happy Anniversary!">{{ events }}</textarea></label>
<small>One per line: month-day, then the message. Christmas, New Year, Mother's Day and Father's Day are built in.</small>
<div class="check" style="margin-top:16px"><input type="checkbox" name="rare_alerts" id="ra" {{ 'checked' if c.easter.rare_alerts }}><label for="ra">Flash a gold alert for rare aircraft</label></div>
<label>Also watch for these types or callsigns
<input type="text" name="watch" value="{{ watch }}" placeholder="e.g. A388, RRR" autocomplete="off"></label>
<small>Aircraft type codes (A388) or callsign starts (RRR). Separate with commas.</small>
<div class="check" style="margin-top:16px"><input type="checkbox" name="milestones" id="ms" {{ 'checked' if c.easter.milestones }}><label for="ms">Celebrate spotting milestones</label></div>
<small>{{ spotted }} aircraft spotted so far. Celebrations at 50, 100, 500, 1000, then every 1000.</small>
<button type="submit" form="resetform" formaction="/spotted/reset" class="secondary" onclick="return confirm('Reset the plane count to 0?')">Reset plane count</button>
</section>
</form>

<form method="post" action="/wifi" class="card" onsubmit="return confirm('The board will disconnect while it switches Wi-Fi. Continue?')">
<h2>Wi-Fi</h2>
<div class="inline">
<label>Network name (SSID)<input type="text" name="ssid" autocapitalize="none" autocomplete="off"></label>
<label>Password<input type="password" name="password" autocomplete="off"></label>
<button>Switch network</button>
</div>
<small>Only use this to move the board to a new network. Check the name and password carefully: if they're wrong the board may drop offline.</small>
</form>

<form id="resetform" method="post"></form>
<div class="savebar"><button type="submit" form="mainform">Save &amp; apply</button></div>
</div></body></html>"""


def _short_host() -> str:
    """The board's name without any '.local' (macOS reports 'name.local', the Pi reports 'name')."""
    return socket.gethostname().split(".")[0]


def _int(form, key, default, lo, hi):
    try:
        return max(lo, min(hi, int(float(form.get(key, default)))))
    except (TypeError, ValueError):
        return default


def _hhmm(value, default):
    try:
        h, m = str(value).split(":")
        return f"{int(h):02d}:{int(m):02d}" if 0 <= int(h) < 24 and 0 <= int(m) < 60 else default
    except ValueError:
        return default


def apply_form(cfg: dict, form) -> tuple[dict, str]:
    """Returns (new_cfg, error). On a place that can't be found the rest still saves; error says why."""
    err = ""
    q = (form.get("location") or "").strip()[:80]
    if q != cfg.get("location") or cfg.get("lat") is None:
        if q:
            hit = data.geocode(q)
            if hit:
                cfg["location"], cfg["place"], cfg["lat"], cfg["lon"] = q, hit["name"], hit["lat"], hit["lon"]
            else:
                err = f"Couldn't find '{q}' (or the map service is unreachable). Location unchanged."
        else:
            cfg["location"], cfg["place"], cfg["lat"], cfg["lon"] = "", "", None, None
    cfg["radius_nm"] = _int(form, "radius_nm", cfg["radius_nm"], 1, 100)
    cfg["brightness"] = _int(form, "brightness", cfg["brightness"], 1, 100)
    cfg["include_ground"] = "include_ground" in form
    cfg["mode"] = form.get("mode") if form.get("mode") in ("rotate", "nearest") else cfg["mode"]
    cfg["dwell_secs"] = _int(form, "dwell_secs", cfg["dwell_secs"], 5, 120)
    sp, al = form.get("speed"), form.get("altitude")
    cfg["units"] = {"speed": sp if sp in ("mph", "kt", "kmh") else cfg["units"]["speed"],
                    "altitude": al if al in ("ft", "m") else cfg["units"]["altitude"]}
    n = cfg["night"]
    cfg["night"] = {"enabled": "night_enabled" in form,
                    "start": _hhmm(form.get("night_start"), n["start"]),
                    "end": _hhmm(form.get("night_end"), n["end"]),
                    "brightness": _int(form, "night_brightness", n["brightness"], 0, 100)}
    cfg["name"] = (form.get("name") or "").strip()[:20]
    cfg["greeting"] = (form.get("greeting") or "").strip()[:40]
    cfg["events"] = easter.parse_events(form.get("events") or "")
    watch = [w.strip().upper() for w in (form.get("watch") or "").replace(";", ",").split(",") if w.strip()][:30]
    cfg["easter"] = {"rare_alerts": "rare_alerts" in form, "milestones": "milestones" in form,
                     "watch_types": [w for w in watch if len(w) <= 4 and any(ch.isdigit() for ch in w)],
                     "watch_callsigns": [w for w in watch if not (len(w) <= 4 and any(ch.isdigit() for ch in w))]}
    return cfg, err


def _state_path():
    return CONFIG_PATH.with_name("state.json")


def _render(ok="", err="", spotted=None):
    c = config.load(CONFIG_PATH)
    events = "\n".join(f"{e['date']} {e['text']}" for e in c.get("events", []))
    watch = ", ".join(c["easter"].get("watch_types", []) + c["easter"].get("watch_callsigns", []))
    return render_template_string(PAGE, c=c, host=_short_host(), ip=local_ip(), ok=ok, err=err,
                                  events=events, watch=watch, spotted=spotted if spotted is not None else f"{easter.read_count(_state_path()):,}")


@app.get("/")
def index():
    return _render()


@app.post("/")
def save():
    cfg, err = apply_form(config.load(CONFIG_PATH), request.form)
    config.save(cfg, CONFIG_PATH)
    return _render(ok="Saved. The board will update within a second or two.", err=err)


def _switch_wifi(ssid: str, password: str):
    cmd = ["nmcli", "device", "wifi", "connect", ssid] + (["password", password] if password else [])
    try:
        subprocess.run(cmd, timeout=60, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


FLIGHT_RE = re.compile(r"^[A-Z0-9]{2,3}\d{1,4}[A-Z]?$")


@app.post("/track")
def track():
    cfg = config.load(CONFIG_PATH)
    if request.form.get("stop"):
        cfg["track"] = {"flight": "", "started": None}
        config.save(cfg, CONFIG_PATH)
        return _render(ok="Stopped tracking. Back to spotting aircraft overhead.")
    flight = re.sub(r"\s+", "", request.form.get("flight", "")).upper()
    if not FLIGHT_RE.match(flight):
        return _render(err="Enter an airline code and number, like BA11 or EZY8271.")
    info = data.lookup_flight(flight)
    if not info:
        return _render(err=f"Couldn't find a route for {flight}. Check the number, or try again if the board is offline.")
    cfg["track"] = {"flight": flight, "started": time.time()}
    config.save(cfg, CONFIG_PATH)
    return _render(ok=f"Tracking {flight}: {info['origin_name']} to {info['destination_name']}.")


@app.post("/spotted/reset")
def reset_spotted():
    """Ask the display to zero the plane count. The display owns the count file, so we leave it a note in the config."""
    cfg = config.load(CONFIG_PATH)
    cfg["spotted_reset"] = f"{time.time():.3f}"
    config.save(cfg, CONFIG_PATH)
    return _render(ok="Plane count reset to 0. Milestones start again from 50.", spotted="0")


@app.post("/wifi")
def wifi():
    ssid, password = request.form.get("ssid", "").strip(), request.form.get("password", "")
    if not shutil.which("nmcli"):
        return _render(err="Wi-Fi switching needs NetworkManager (nmcli), which isn't available here.")
    if not 1 <= len(ssid) <= 32 or (password and not 8 <= len(password) <= 63):
        return _render(err="Network name must be 1-32 characters and the password 8-63 (or blank for an open network).")
    threading.Timer(1.5, _switch_wifi, (ssid, password)).start()  # let this response go out first
    return _render(ok=f"Switching to '{ssid}'. If this page stops responding, reconnect your phone to the same network "
                      f"and open http://{_short_host()}.local in a minute.")


def main():
    app.run(host="0.0.0.0", port=int(os.environ.get("FLIGHTBOARD_PORT", 80)))


if __name__ == "__main__":
    main()
