"""The display program: poll for aircraft in a background thread, render frames, push to the matrix.

  sudo python -m flightboard.display            # on the Pi (needs root for the matrix)
  sudo python -m flightboard.display --test     # Pi: test pattern for first power-up
  python -m flightboard.display --preview       # on a Mac: writes out/display.png about once a second
  python -m flightboard.display --simulate [--demo]   # on a Mac: live view at http://localhost:8765
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Optional

from PIL import Image

from . import config, data, tracker
from . import easter
from .clock import effective_brightness
from .render import Renderer, led_look, slide
from .samples import SAMPLES

log = logging.getLogger("flightboard")

FPS = 15
TRANSITION_SECS = 0.6
STALE_SECS = 45          # keep showing the last aircraft this long if the API is failing
OFFLINE_AFTER = 3        # consecutive failed polls before showing "No connection"
SWITCH_HYSTERESIS_NM = 0.5
BOOT_SECS = 8
BOOT_WAIT_FOR_IP_SECS = 40


def local_ip() -> Optional[str]:
    """The LAN address, or None if there isn't one yet. UDP connect sends no packets."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None
    finally:
        s.close()


class State:
    """Shared between the fetch thread and the render loop."""

    def __init__(self):
        self.lock = threading.Lock()
        self.aircraft: Optional[data.Aircraft] = None
        self.last_ok = 0.0
        self.failures = 0
        self.configured = False
        self.track = None  # (Aircraft, TrackInfo) while following a flight

    def snapshot(self):
        with self.lock:
            return self.aircraft, self.last_ok, self.failures, self.configured, self.track


class Fetcher(threading.Thread):
    def __init__(self, state: State, get_cfg, stop: threading.Event):
        super().__init__(daemon=True)
        self.state, self.get_cfg, self.stop = state, get_cfg, stop
        self.routes = data.RouteLookup()
        self.aircraft_info = data.AircraftLookup()
        self.shown_since, self.last_shown = 0.0, {}
        self.pool, self.pool_time = [], 0.0
        self.tracker = tracker.Tracker(config.DEFAULT_PATH.with_name("track.json"))

    def run(self):
        while not self.stop.is_set():
            wait = 5
            try:
                wait = self.poll_once()
            except Exception:
                log.exception("fetch loop error")  # never let this thread die
            end = time.time() + wait
            while not self.stop.is_set() and time.time() < end:
                self.stop.wait(max(0.0, min(1.0, end - time.time())))
                try:
                    self._tick()
                except Exception:
                    log.exception("rotation tick error")

    def _tick(self, now=None):
        """Between polls: when the dwell time is up, move on to the next aircraft from the last poll.
        Without this, switching could only happen at poll times, so "10 s" would really mean 16 s."""
        cfg = self.get_cfg()
        now = time.time() if now is None else now
        if (cfg.get("mode", "rotate") != "rotate" or not self.pool or (cfg.get("track") or {}).get("flight")
                or now - self.pool_time > 45 or now - self.shown_since < cfg.get("dwell_secs", 30)):
            return
        self._apply(self.pool, cfg, now, fresh=False)

    def _choose(self, pool, current, cfg, now):
        """Pick what to show from `pool` (nearest first).

        "nearest": the closest aircraft, sticking with the current one unless another is clearly closer.
        "rotate": cycle through the nearest few, `dwell_secs` each, least recently shown first.
        """
        if not pool:
            return None
        cur_hex = current.hex if current else None
        if cfg.get("mode", "rotate") == "nearest":
            same = next((a for a in pool if a.hex == cur_hex), None)
            if same and pool[0].hex != same.hex and pool[0].distance_nm > same.distance_nm - SWITCH_HYSTERESIS_NM:
                return same
            return pool[0]
        top = pool[: max(1, int(cfg.get("rotate_count", 6)))]
        same = next((a for a in top if a.hex == cur_hex), None)
        if same and now - self.shown_since < cfg.get("dwell_secs", 30):
            return same
        pick = min(top, key=lambda a: (self.last_shown.get(a.hex, 0.0), a.distance_nm))
        self.shown_since = self.last_shown[pick.hex] = now
        self.last_shown = {h: t for h, t in self.last_shown.items() if now - t < 3600}
        return pick

    def _apply(self, pool, cfg, now, fresh: bool):
        """Choose from `pool`, add route/type details, and publish to the display. `fresh` = just polled."""
        with self.state.lock:
            current = self.state.aircraft
        chosen = self._choose(pool, current, cfg, now)
        if chosen:
            if current and current.hex == chosen.hex:  # keep enrichment, take fresh telemetry
                data.copy_route(current, chosen)
                if not chosen.route:
                    self.routes.enrich(chosen)
                if not chosen.type_full:
                    self.aircraft_info.enrich(chosen)
            else:
                self.routes.enrich(chosen)
                self.aircraft_info.enrich(chosen)
        if chosen and (not current or current.hex != chosen.hex):
            log.info("showing %s %s | %s | %s | %.1f nm", chosen.callsign or chosen.registration or chosen.hex,
                     chosen.route or "no route", chosen.operator or "-", chosen.type_name or "-", chosen.distance_nm)
        with self.state.lock:
            self.state.aircraft = chosen
            if fresh:
                self.state.last_ok, self.state.failures = time.time(), 0

    def poll_once(self) -> float:
        cfg = self.get_cfg()
        lat, lon = cfg.get("lat"), cfg.get("lon")
        with self.state.lock:
            self.state.configured = lat is not None and lon is not None
        trk = cfg.get("track") or {}
        if trk.get("flight"):
            ac, info, ok = self.tracker.step(trk["flight"], trk.get("started"), dt.datetime.now())
            with self.state.lock:
                self.state.track = (ac, info) if info is not None else None
                if ok:
                    self.state.failures, self.state.last_ok = 0, time.time()
                else:
                    self.state.failures += 1
            return max(5, cfg["poll_secs"])
        with self.state.lock:
            self.state.track = None
        if lat is None or lon is None:
            return 2
        found = data.fetch_nearby(lat, lon, cfg["radius_nm"])
        if found is None:
            with self.state.lock:
                self.state.failures += 1
            log.warning("fetch failed (%d in a row)", self.state.failures)
            return min(30, cfg["poll_secs"] * (1 + min(self.state.failures, 4)))  # back off
        pool = sorted((a for a in found if cfg.get("include_ground") or not a.on_ground), key=lambda a: a.distance_nm)
        self.pool, self.pool_time = pool, time.time()
        self._apply(pool, cfg, self.pool_time, fresh=True)
        return cfg["poll_secs"]


# ------------------------------------------------------------------ outputs

class PreviewOutput:
    def __init__(self, path: Path):
        self.path, self._last = path, 0.0
        path.parent.mkdir(exist_ok=True)

    def show(self, img: Image.Image, brightness: int):
        if time.time() - self._last < 1:
            return
        self._last = time.time()
        dim = img.point(lambda v: int(v * brightness / 100))
        dim.resize((img.width * 6, img.height * 6), Image.NEAREST).save(self.path)

    def close(self):
        pass


PAGE = b"""<!doctype html><meta charset=utf-8><title>Flight Board simulator</title>
<body style="margin:0;background:#161616;color:#aaa;font:14px sans-serif;text-align:center">
<p><label><input type=checkbox id=dots checked> LED dots</label> &nbsp; edit code/config.json and refresh the code with Ctrl-C + rerun; config changes apply live</p>
<img id=f style="width:min(96vw,1024px);image-rendering:pixelated;border:8px solid #000">
<script>const f=document.getElementById('f'),d=document.getElementById('dots');
setInterval(()=>{f.src='/frame.png?dots='+(d.checked?1:0)+'&t='+Date.now()},100)</script>"""


class SimOutput:
    """Serves the live frame to a browser at http://localhost:PORT. Same loop as the real thing."""

    def __init__(self, port: int):
        import http.server, io

        outer = self
        self.png = b""
        self.raw: Optional[Image.Image] = None

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/frame.png") and outer.raw is not None:
                    img = led_look(outer.raw) if "dots=1" in self.path else outer.raw.resize((1024, 512), Image.NEAREST)
                    buf = io.BytesIO()
                    img.save(buf, "PNG")
                    body, ctype = buf.getvalue(), "image/png"
                else:
                    body, ctype = PAGE, "text/html"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        log.info("simulator at http://localhost:%d", port)

    def show(self, img: Image.Image, brightness: int):
        self.raw = img.point(lambda v: int(v * brightness / 100))

    def close(self):
        self.server.shutdown()


def _demo_track():
    now = dt.datetime.now()
    base = dict(SAMPLES["ba_long"], callsign="BAW11", lat=50.5, lon=-1.0)
    left = now - dt.timedelta(minutes=40)
    steps = [("waiting", None, 0, 0, True), ("climbing", 0.06, 12400, 330, False), ("cruising", 0.35, 36000, 489, False),
             ("cruising", 0.7, 36000, 489, False), ("descending", 0.93, 5200, 250, False), ("landed", 1.0, 0, 0, True)]
    for phase, prog, alt, gs, ground in steps:
        ac = data.Aircraft(**{**base, "alt_ft": alt, "gs_kt": gs, "on_ground": ground})
        info = data.TrackInfo(flight="BA11", phase=phase, progress=prog, departed_at=None if phase == "waiting" else left,
                              remaining_nm=None if prog is None else 200 * (1 - prog) + 5,
                              eta=None if prog is None or phase == "landed" else now + dt.timedelta(minutes=int(60 * (1 - prog)) + 5),
                              landed_at=now if phase == "landed" else None)
        yield ac, info


class DemoFeeder(threading.Thread):
    """Stands in for the Fetcher: cycles sample aircraft, with a gap showing the idle screen."""

    def __init__(self, state: State, stop: threading.Event):
        super().__init__(daemon=True)
        self.state, self.stop = state, stop

    def run(self):
        i = 0
        while not self.stop.is_set():
            kws = list(SAMPLES.values())
            ac = data.Aircraft(**kws[i % len(kws)]) if i % (len(kws) + 1) != len(kws) else None
            with self.state.lock:
                self.state.aircraft, self.state.last_ok, self.state.failures, self.state.configured = ac, time.time(), 0, True
                self.state.track = None
            i = (i + 1) % (len(kws) + 1)
            self.stop.wait(8)
            if i == 0:  # after each lap of samples, show a followed flight from take-off to landing
                for ac2, info in _demo_track():
                    with self.state.lock:
                        self.state.aircraft, self.state.track = None, (ac2, info)
                    if self.stop.wait(5):
                        return
                with self.state.lock:
                    self.state.track = None


class MatrixOutput:
    def __init__(self, hw: dict, brightness: int):
        from rgbmatrix import RGBMatrix, RGBMatrixOptions  # only exists on the Pi

        o = RGBMatrixOptions()
        o.rows, o.cols, o.chain_length = hw["rows"], hw["cols"], hw["chain_length"]
        o.hardware_mapping = hw["hardware_mapping"]
        o.gpio_slowdown = hw["gpio_slowdown"]
        o.brightness = max(1, brightness)
        o.drop_privileges = False  # the display writes state.json (spotted count) after the matrix starts
        # Optional extras for panels that need them (see README "If the panel looks wrong")
        for key in ("panel_type", "multiplexing", "row_address_type", "pixel_mapper_config", "pwm_bits", "scan_mode"):
            if hw.get(key) not in (None, ""):
                setattr(o, key, hw[key])
        self.matrix = RGBMatrix(options=o)
        self.canvas = self.matrix.CreateFrameCanvas()
        self._bright = None

    def show(self, img: Image.Image, brightness: int):
        if brightness != self._bright:
            self._bright = brightness
            self.matrix.brightness = max(0, brightness)
        self.canvas.SetImage(img)
        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def close(self):
        self.matrix.Clear()


# --------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="no hardware: write out/display.png")
    ap.add_argument("--simulate", action="store_true", help="no hardware: live view in your browser")
    ap.add_argument("--demo", action="store_true", help="with --simulate: cycle sample aircraft instead of live data")
    ap.add_argument("--test", action="store_true", help="show a hardware test pattern and nothing else")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--config", type=Path, default=config.DEFAULT_PATH)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg_box = {"cfg": config.load(args.config), "mtime": config.mtime(args.config)}
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    cfg = cfg_box["cfg"]
    if args.simulate:
        out = SimOutput(args.port)
    elif args.preview:
        out = PreviewOutput(Path("out/display.png"))
    else:
        out = MatrixOutput(cfg["hardware"], cfg["brightness"])
    renderer = Renderer()
    if args.test:  # hardware bring-up: check orientation, order and that every pixel lights
        out.show(renderer.test_pattern(), 100)
        log.info("test pattern showing; Ctrl-C to stop")
        stop.wait()
        out.close()
        return
    spot = easter.SpotLog(Path(os.environ.get("FLIGHTBOARD_STATE", args.config.with_name("state.json"))))
    takeover = None  # (until, kind, text): milestone celebration
    counted_hex = None
    last_err_log = 0.0
    state = State()
    if args.demo:
        DemoFeeder(state, stop).start()
    else:
        Fetcher(state, lambda: cfg_box["cfg"], stop).start()

    host = socket.gethostname().split(".")[0]
    boot_start = time.time()
    prev_frame: Optional[Image.Image] = None
    shown_key, trans_start = None, 0.0
    t0 = time.time()
    log.info("display started (%s)", "simulator" if args.simulate else "preview" if args.preview else "matrix")

    try:
        while not stop.is_set():
            frame_start = time.time()
            t = frame_start - t0

            m = config.mtime(args.config)
            if m != cfg_box["mtime"]:  # settings page saved something
                cfg_box["cfg"], cfg_box["mtime"] = config.load(args.config), m
                log.info("config reloaded")
            cfg = cfg_box["cfg"]
            if spot.apply_reset(cfg.get("spotted_reset")):
                counted_hex, takeover = None, None
                log.info("plane count reset")
            now = dt.datetime.now()
            bright = effective_brightness(cfg, now)

            ip = local_ip()
            booting = (frame_start - boot_start) < BOOT_SECS or (ip is None and (frame_start - boot_start) < BOOT_WAIT_FOR_IP_SECS)
            ac, last_ok, failures, configured, track = state.snapshot()
            offline = failures >= OFFLINE_AFTER
            fresh = ac is not None and (frame_start - last_ok) < STALE_SECS

            try:
                if booting:
                    lines = ["Flight Board", f"{host}.local", ip or "Waiting for Wi-Fi...", "Settings on that address"]
                    if (cfg.get("greeting") or "").strip():
                        lines = [cfg["greeting"].strip()] + lines[:3]
                    key, img = "boot", renderer.message(lines)
                elif track is not None:
                    key, img = "track", renderer.tracked(track[0], track[1], cfg, t, offline)
                elif not configured:
                    key, img = "setup", renderer.message(["Set your location", "at", f"{host}.local"])
                elif takeover and frame_start < takeover[0]:
                    key, img = "milestone", renderer.celebration(takeover[1], takeover[2], t)
                elif fresh:
                    if ac.hex != counted_hex and (cfg.get("easter") or {}).get("milestones", True):
                        counted_hex = ac.hex
                        n, hit = spot.record(ac.hex, time.time())
                        if hit:
                            takeover = (frame_start + 12, "milestone", f"You've spotted {n:,} planes!")
                            log.info("milestone: %d", n)
                    key, img = ac.hex, renderer.aircraft(ac, cfg, t, offline, now, easter.rarity(ac, cfg))
                else:
                    ev = easter.active_event(cfg, now.date())
                    if ev and int(t // 8) % 2 == 1:  # alternate the clock and today's message
                        key, img = "event", renderer.celebration(ev[0], ev[1], t)
                    else:
                        key, img = "idle", renderer.idle(cfg, now, t, offline, spot.count)
            except Exception:  # one bad frame must never take the display down: show a notice and carry on
                if time.time() - last_err_log > 30:
                    log.exception("could not draw frame")
                    last_err_log = time.time()
                key, img = "error", renderer.message(["Flight Board", "Display error", "See the logs"])

            if key != shown_key and prev_frame is not None:
                trans_start = frame_start
            shown_key = key
            if frame_start - trans_start < TRANSITION_SECS and prev_frame is not None:
                shown = slide(prev_frame, img, (frame_start - trans_start) / TRANSITION_SECS)
            else:
                shown, prev_frame = img, img
            out.show(shown, bright)
            if shown is img:
                prev_frame = img

            stop.wait(max(0.0, 1.0 / FPS - (time.time() - frame_start)))
    finally:
        spot.flush()  # save the spotted count on a clean shutdown
        out.close()
        log.info("display stopped")


if __name__ == "__main__":
    main()
