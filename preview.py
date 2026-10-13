#!/usr/bin/env python3
"""Render the board layout to PNG on your Mac. No Pi, no hardware.

  python preview.py                 # sample scenarios -> out/*.png and out/contact-sheet.png
  python preview.py --live          # nearest real aircraft for the lat/lon in config.json -> out/live.png
  python preview.py --live --lat 51.47 --lon -0.45 --radius 20
"""
import argparse
import datetime as dt
from pathlib import Path

from PIL import Image

from flightboard import config, data
from flightboard.render import H, W, Renderer, led_look
from flightboard.samples import SAMPLES

OUT = Path(__file__).parent / "out"
SCALE = 8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--radius", type=float)
    ap.add_argument("--units", choices=["imperial", "kt", "metric"], default="imperial")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    cfg = config.load()
    cfg["units"] = {"imperial": {"speed": "mph", "altitude": "ft"},
                    "kt": {"speed": "kt", "altitude": "ft"},
                    "metric": {"speed": "kmh", "altitude": "m"}}[args.units]
    r = Renderer()

    if args.live:
        lat = args.lat if args.lat is not None else cfg["lat"]
        lon = args.lon if args.lon is not None else cfg["lon"]
        if lat is None or lon is None:
            raise SystemExit("No location: pass --lat/--lon or set it in config.json")
        found = data.fetch_nearby(lat, lon, args.radius or cfg["radius_nm"])
        ac = data.pick_nearest(found or [])
        if not ac:
            frame = r.idle(cfg, dt.datetime.now())
            print("Nothing overhead (or fetch failed): rendered the idle screen")
        else:
            data.RouteLookup().enrich(ac)
            frame = r.aircraft(ac, cfg)
            print(f"{ac.callsign or ac.hex} {ac.airline} {ac.route} {ac.type_name} {ac.distance_nm:.1f}nm")
        led_look(frame).save(OUT / "live.png")
        print("wrote", OUT / "live.png")
        return

    frames = {}
    for name, kw in SAMPLES.items():
        frames[name] = r.aircraft(data.Aircraft(**kw), cfg)
    frames["idle"] = r.idle(cfg, dt.datetime(2026, 10, 9, 14, 37))
    frames["idle_offline"] = r.idle(cfg, dt.datetime(2026, 10, 9, 14, 37), offline=True)
    frames["boot"] = r.message(["Flight Board", "flightboard.local", "192.168.1.42", "Settings page ready"])
    for name, f in frames.items():
        led_look(f).save(OUT / f"{name}.png")
    cols, pad = 2, 12
    s = SCALE // 2
    thumbs = [led_look(f, s) for f in frames.values()]
    tw, th = thumbs[0].size
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + pad) + pad), (30, 30, 30))
    for i, t in enumerate(thumbs):
        sheet.paste(t, (pad + (i % cols) * (tw + pad), pad + (i // cols) * (th + pad)))
    sheet.save(OUT / "contact-sheet.png")
    print("wrote", len(frames), "frames and", OUT / "contact-sheet.png")


if __name__ == "__main__":
    main()
