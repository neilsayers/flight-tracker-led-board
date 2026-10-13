"""Render docs/preview.png for the README from the project's own renderer.

Uses an empty logo folder on purpose: the picture must not contain any airline's trademarked logo.
Run from the project root:  python3 scripts/make-readme-image.py
"""
import datetime as dt
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from flightboard import config  # noqa: E402
from flightboard.data import Aircraft, TrackInfo  # noqa: E402
from flightboard.render import Renderer, led_look  # noqa: E402
from flightboard.samples import SAMPLES  # noqa: E402

with tempfile.TemporaryDirectory() as empty:
    r = Renderer(logo_dir=Path(empty))
    cfg = dict(config.DEFAULTS, name="Sam", greeting="Hi Sam!")
    today = dt.datetime(2026, 6, 20, 14, 37)
    ac = lambda key, **kw: Aircraft(**{**SAMPLES[key], **kw})
    left = today - dt.timedelta(hours=1, minutes=5)
    tracked = ac("ba_long", callsign="BAW11", lat=50.2, lon=-1.0, alt_ft=36000, gs_kt=489)
    frames = [
        r.aircraft(ac("united"), cfg, t=0.4, now=today),
        r.tracked(tracked, TrackInfo(flight="BA11", phase="cruising", progress=0.58, departed_at=left,
                                     remaining_nm=95, eta=today + dt.timedelta(minutes=48)), cfg, t=0.4),
        r.aircraft(ac("police_helicopter"), cfg, t=0.4, now=today),
        r.aircraft(ac("raf_typhoon"), cfg, t=0.4, now=today),
        r.aircraft(ac("glider"), cfg, t=0.4, now=today),
        r.aircraft(ac("balloon"), cfg, t=0.4, now=today),
        r.celebration("milestone", "You've spotted 100 planes!", 0.7),
        r.idle(cfg, today, t=7, spotted=137),
    ]
    tiles = [led_look(f, 3) for f in frames]

w, h = tiles[0].size
pad, cols = 12, 2
rows = (len(tiles) + cols - 1) // cols
sheet = Image.new("RGB", (cols * (w + pad) + pad, rows * (h + pad) + pad), (24, 24, 24))
for i, t in enumerate(tiles):
    sheet.paste(t, (pad + (i % cols) * (w + pad), pad + (i // cols) * (h + pad)))
(ROOT / "docs").mkdir(exist_ok=True)
sheet.save(ROOT / "docs" / "preview.png", optimize=True)
print("wrote docs/preview.png", sheet.size)
