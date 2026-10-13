"""Draws 128x64 frames with Pillow. No hardware imports: used by the display and the Mac preview."""
from __future__ import annotations

import colorsys
import datetime as dt
import hashlib
import math
import unicodedata
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from .data import Aircraft, TrackInfo, haversine_nm
from .countries import country_of
from .easter import time_greeting
from .kinds import KIND_LABELS

W, H = 128, 64
ROOT = Path(__file__).resolve().parent
FONT_DIR = ROOT / "fonts"
LOGO_DIR = ROOT.parent / "logos"

LOGO_SIZE = 46
TEXT_X = LOGO_SIZE + 4
TEXT_W = W - TEXT_X

WHITE = (255, 255, 255)
AMBER = (255, 176, 0)  # idle clock only
CYAN = (0, 200, 255)
DATA = (235, 245, 255)
DIM = (90, 90, 90)
RED = (255, 40, 40)
GOLD = (255, 205, 40)
GREEN = (80, 255, 120)
MIL_GREEN = (110, 200, 90)  # military icons
BADGE = 14                  # emblem size, drawn inside a framed box in the corner of the 46 px logo box
BADGE_BOX = BADGE + 4       # the framed box: 1 px grey outline + 1 px padding each side
BADGE_FRAME = (120, 120, 120)
# Callsigns that map to another operator's logo in the collection
MIL_LOGO_ALIASES = {"ASCOT": "RRR", "RAFAIR": "RRR", "REACH": "RCH", "FAF": "CTM"}

MPH, KMH = 1.15078, 1.852  # per knot
FT_PER_M = 3.28084


# ---------------------------------------------------------------- formatting

def fmt_alt(ft: Optional[float], unit: str = "ft", on_ground: bool = False) -> str:
    if on_ground:
        return "ground"
    if ft is None:
        return "--"
    v = ft / FT_PER_M if unit == "m" else ft
    if abs(v) >= 1000:
        k = v / 1000
        return f"{k:.0f}k {unit}" if k >= 10 else f"{k:.1f}k {unit}"
    return f"{v:.0f} {unit}"


def fmt_speed(kt: Optional[float], unit: str = "mph") -> str:
    if kt is None:
        return "--"
    if unit == "kt":
        return f"{kt:.0f}kt"
    if unit == "kmh":
        return f"{kt * KMH:.0f}kmh"
    return f"{kt * MPH:.0f}mph"


def fmt_vrate(fpm: Optional[float], alt_unit: str = "ft") -> str:
    if fpm is None:
        return "--"
    if alt_unit == "m":
        return f"{fpm / 60 / FT_PER_M:.0f}m/s"
    return f"{fpm / 60:.0f}ft/s"


def fmt_track(deg: Optional[float]) -> str:
    return "--" if deg is None else f"{deg % 360:.0f}deg"


_SUFFIXES = (" aoc", " airlines", " airways", " air lines", " airline", " limited", " ltd", " inc", " plc")


def short_airline(name: str) -> str:
    """'United Airlines' -> 'United', as in the reference photo. Keeps the name if stripping empties it."""
    out, changed = name.strip(), True
    while changed:
        changed = False
        for suf in _SUFFIXES:
            if out.lower().endswith(suf) and len(out) > len(suf):
                out, changed = out[: -len(suf)].rstrip(" ,."), True
    return out


def airline_row(ac: Aircraft) -> str:
    """Top row: operator, or role, or registration. Military aircraft get '(Military)' after the name."""
    name = short_airline(ac.operator) or "Private flight"
    return f"{name} (Military)" if ac.is_military and name != "Military" else name


def type_row(ac: Aircraft) -> str:
    """Third row: the type name; for things with no useful type (balloons, gliders, drones) a label for the kind."""
    name = ac.type_name
    if (not name or name == ac.type_code) and ac.kind in KIND_LABELS:
        return KIND_LABELS[ac.kind]
    return name or ac.registration or ""


def eta_text(ac: Aircraft, now: dt.datetime) -> str:
    """Arrival clock time (board's local time), from straight-line distance at current ground speed. Rough."""
    if ac.on_ground or ac.dest_lat is None or ac.dest_lon is None or not ac.gs_kt or ac.gs_kt < 100:
        return ""
    hours = haversine_nm(ac.lat, ac.lon, ac.dest_lat, ac.dest_lon) / ac.gs_kt
    return (now + dt.timedelta(hours=hours)).strftime("%H:%M")


def route_line(ac: Aircraft, now: dt.datetime) -> str:
    if not (ac.origin_name and ac.destination_name):
        if ac.owner:
            return f"{ac.owner} ({ac.registration})" if ac.registration else ac.owner
        return "Route not available"
    eta = eta_text(ac, now)
    return f"Dep: {ac.origin_name}, Dest: {ac.destination_name}" + (f" (ETA: {eta})" if eta else "")


def _dist(nm: float, speed_unit: str) -> str:
    if speed_unit == "kt":
        return f"{nm:,.0f} nm"
    if speed_unit == "kmh":
        return f"{nm * KMH:,.0f} km"
    return f"{nm * MPH:,.0f} mi"


def _hm(delta: dt.timedelta) -> str:
    mins = int(delta.total_seconds() // 60)
    return f"{mins // 60}h {mins % 60:02d}m" if mins >= 60 else f"{mins}m"


def track_line(ac: Aircraft, trk: TrackInfo, units: dict, now: Optional[dt.datetime] = None) -> str:
    """The scrolling status row for a followed flight. Uses only what the free data sources provide."""
    su = units.get("speed", "mph")
    if trk.phase == "waiting":
        return f"Tracking {trk.flight}: waiting for departure" + (f" from {ac.origin_name}" if ac.origin_name else "")
    if trk.phase == "landed":
        bits = [f"Landed at {ac.destination_name or ac.destination or 'destination'}" + (f" {trk.landed_at:%H:%M}" if trk.landed_at else "")]
        if trk.departed_at and trk.landed_at:
            bits.append(f"Flight time {_hm(trk.landed_at - trk.departed_at)}")
        return " | ".join(bits)
    bits = []
    if trk.phase == "lost":
        ago = f" {_hm((now or dt.datetime.now()) - trk.last_seen)} ago" if trk.last_seen else ""
        bits.append(f"Signal lost, last seen{ago}")
    if trk.progress is not None:
        bits.append(f"{round(trk.progress * 100)}%")
    if trk.departed_at:
        bits.append(f"Left {ac.origin or 'origin'} {trk.departed_at:%H:%M}")
    bits.append({"climbing": "Climbing", "cruising": "Cruising", "descending": "Descending"}.get(trk.phase, ""))
    if trk.remaining_nm is not None and trk.phase != "lost":
        bits.append(f"{_dist(trk.remaining_nm, su)} to go")
    if trk.eta and trk.phase != "lost":
        bits.append(f"ETA {trk.eta:%H:%M}")
    return " | ".join(b for b in bits if b)


def data_lines(ac: Aircraft, units: dict) -> tuple[str, str]:
    au, su = units.get("altitude", "ft"), units.get("speed", "mph")
    return (
        f"Alt: {fmt_alt(ac.alt_ft, au, ac.on_ground)}, Spd: {fmt_speed(ac.gs_kt, su)}",
        f"Trk: {fmt_track(ac.track)}, Vr: {fmt_vrate(ac.vrate_fpm, au)}",
    )


# ------------------------------------------------------------------ renderer

_FOLD = {"ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ı": "i", "ħ": "h", "Ħ": "H", "œ": "oe", "Œ": "OE", "ſ": "s"}


def safe_text(text) -> str:
    """The bitmap fonts only have Latin-1 (Western European) glyphs, and drawing anything else raises an error.
    Characters beyond that become their plain letters (ă -> a, ł -> l), or '?' when there is no equivalent."""
    out = []
    for ch in str(text):
        if 32 <= ord(ch) < 256 or ch == "\n":
            out.append(ch)
            continue
        ch = _FOLD.get(ch, ch)
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        out.append(base if base and all(32 <= ord(c) < 256 for c in base) else "?")
    return "".join(out)


class SafeBitmapFont(ImageFont.ImageFont):
    """A Pillow bitmap font that cleans its text first, so odd names from the data feeds can never crash a frame."""

    def getmask(self, text, *a, **k):
        return super().getmask(safe_text(text), *a, **k)

    def getbbox(self, text, *a, **k):
        return super().getbbox(safe_text(text), *a, **k)

    def getlength(self, text, *a, **k):
        return super().getlength(safe_text(text), *a, **k)


def _load_font(name: str) -> ImageFont.ImageFont:
    f = ImageFont.load(str(FONT_DIR / name))
    f.__class__ = SafeBitmapFont
    return f


class Renderer:
    def __init__(self, logo_dir: Path = LOGO_DIR):
        self.logo_dir = Path(logo_dir)
        self.f4 = _load_font("4x6.pil")
        self.f5 = _load_font("5x7.pil")
        self.f6 = _load_font("6x10.pil")
        self.f6b = _load_font("6x12.pil")
        self._logos: dict[str, Image.Image] = {}

    # -- helpers
    @staticmethod
    def _new() -> tuple[Image.Image, ImageDraw.ImageDraw]:
        img = Image.new("RGB", (W, H), (0, 0, 0))
        return img, ImageDraw.Draw(img)

    def _fit(self, text: str, width: int, fonts) -> ImageFont.ImageFont:
        """Largest of `fonts` (big to small) in which `text` fits `width`; else the smallest."""
        for f in fonts:
            if f.getlength(text) <= width:
                return f
        return fonts[-1]

    def _text(self, img, xy, text, font, fill, width=None, t=0.0, speed=18.0):
        """Draw text; if wider than `width`, scroll it (pause, scroll, loop) inside a clip box."""
        draw = ImageDraw.Draw(img)
        if width is None or font.getlength(text) <= width:
            draw.text(xy, text, font=font, fill=fill)
            return
        full = int(font.getlength(text))
        gap = 16
        cycle = 1.5 + (full + gap) / speed  # pause 1.5s, then `speed` px/s
        phase = t % cycle
        off = 0 if phase < 1.5 else int((phase - 1.5) * speed)
        strip = Image.new("RGB", (full * 2 + gap, 10), (0, 0, 0))
        sd = ImageDraw.Draw(strip)
        sd.text((0, 0), text, font=font, fill=fill)
        sd.text((full + gap, 0), text, font=font, fill=fill)
        img.paste(strip.crop((off, 0, off + width, 10)), (int(xy[0]), int(xy[1])))

    def _top_rows(self, img, airline: str, ident_route: str, atype: str, t: float):
        """The three text rows beside the logo. Names (rows 1 and 3) keep the big font and scroll when too long;
        the flight/route row shrinks to fit instead, so the airport codes stay readable without scrolling."""
        self._text(img, (TEXT_X, 2), airline, self.f6, WHITE, TEXT_W, t)
        self._text(img, (TEXT_X, 17), ident_route, self._fit(ident_route, TEXT_W, [self.f6, self.f5, self.f4]), WHITE, TEXT_W, t)
        self._text(img, (TEXT_X, 32), atype, self.f6, WHITE, TEXT_W, t)

    # -- logos
    def logo(self, ac: Aircraft, t: float = 0.0) -> Image.Image:
        if ac.is_military:
            return self._military_logo(ac, t)
        real = self._real_logo(ac)
        return real if real is not None else self._fallback_logo(ac, t)

    def _get_logo(self, code: str) -> Optional[Image.Image]:
        code = (code or "").upper()
        if not code:
            return None
        if code not in self._logos:
            self._logos[code] = self._load_logo(code)
        return self._logos[code]

    def _real_logo(self, ac: Aircraft) -> Optional[Image.Image]:
        for code in (ac.airline_icao, ac.airline_prefix):
            img = self._get_logo(code)
            if img is not None:
                return img
        return None

    def _military_logo(self, ac: Aircraft, t: float) -> Image.Image:
        """Green silhouette of the aircraft type, with the operator's emblem in a small grey-framed box in the corner."""
        tile = Image.new("RGB", (LOGO_SIZE, LOGO_SIZE), (0, 0, 0))
        size = LOGO_SIZE - 8
        mask = _icon_mask(ac.kind, size, ac.track or 0, t)
        tile.paste(Image.composite(Image.new("RGB", mask.size, MIL_GREEN), Image.new("RGB", mask.size), mask), (0, 0))
        badge = self.military_emblem(ac)
        if badge is not None:
            x = y = LOGO_SIZE - BADGE_BOX
            ImageDraw.Draw(tile).rectangle((x, y, x + BADGE_BOX - 1, y + BADGE_BOX - 1), fill=(0, 0, 0), outline=BADGE_FRAME)
            tile.paste(badge, (x + 2, y + 2))
        return tile

    def military_emblem(self, ac: Aircraft) -> Optional[Image.Image]:
        """Operator logo from the collection if there is one, else a drawn national roundel, else None."""
        alias = next((v for k, v in MIL_LOGO_ALIASES.items() if (ac.callsign or "").startswith(k)), "")
        for code in (ac.airline_icao, ac.airline_prefix, alias):
            img = self._get_logo(code)
            if img is not None:
                return img.resize((BADGE, BADGE), Image.LANCZOS)
        return _roundel(country_of(ac.hex), BADGE)

    def _load_logo(self, code: str) -> Optional[Image.Image]:
        p = self.logo_dir / f"{code}.png"
        if not p.is_file():
            return None
        try:
            src = Image.open(p).convert("RGBA")
            src.thumbnail((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
            tile = Image.new("RGB", (LOGO_SIZE, LOGO_SIZE), (0, 0, 0))
            bg = Image.new("RGBA", src.size, (0, 0, 0, 255))
            bg.alpha_composite(src)
            tile.paste(bg.convert("RGB"), ((LOGO_SIZE - src.width) // 2, (LOGO_SIZE - src.height) // 2))
            return tile
        except Exception:
            return None  # a bad PNG must never take the display down

    def _fallback_logo(self, ac: Aircraft, t: float = 0.0) -> Image.Image:
        """No real logo: a coloured tile with the airline code for ordinary airliners, otherwise an icon for the kind
        of aircraft (helicopter, glider, business jet, ...) pointing the way it is heading."""
        tile = Image.new("RGB", (LOGO_SIZE, LOGO_SIZE), (0, 0, 0))
        code = ac.airline_icao or ac.airline_prefix
        if ac.kind == "airliner" and code:
            d = ImageDraw.Draw(tile)
            hue = int(hashlib.md5(code.encode()).hexdigest()[:4], 16) / 0xFFFF
            r, g, b = colorsys.hsv_to_rgb(hue, 0.75, 0.75)
            d.rounded_rectangle((0, 0, LOGO_SIZE - 1, LOGO_SIZE - 1), radius=6, fill=(int(r * 255), int(g * 255), int(b * 255)))
            w = self.f6b.getlength(code)
            d.text(((LOGO_SIZE - w) / 2, (LOGO_SIZE - 10) / 2), code, font=self.f6b, fill=WHITE)
            return tile
        mask = _icon_mask(ac.kind, LOGO_SIZE, ac.track or 0, t)
        return Image.composite(Image.new("RGB", mask.size, AMBER), tile, mask)

    # -- frames
    def aircraft(self, ac: Aircraft, cfg: dict, t: float = 0.0, offline: bool = False,
                 now: Optional[dt.datetime] = None, rare: Optional[str] = None) -> Image.Image:
        img, d = self._new()
        img.paste(self.logo(ac, t), (1, 0))
        now = now or dt.datetime.now()

        airline = airline_row(ac)
        ident = ac.callsign or ac.registration or ac.hex.upper()
        ident_route = f"{ident} {ac.route}" if ac.route else ident
        atype = type_row(ac)
        self._top_rows(img, airline, ident_route, atype, t)

        d.line((0, 46, W - 1, 46), fill=(35, 35, 35))
        line = route_line(ac, now)
        if rare:
            line = f"RARE SIGHTING! {rare}  *  " + line
        self._text(img, (1, 48), line, self.f5, GOLD if rare else WHITE, W - 2, t, speed=26.0)
        alt_spd, _ = data_lines(ac, cfg.get("units", {}))
        d.text((1, 56), alt_spd, font=self._fit(alt_spd, W - 2, [self.f5, self.f4]), fill=DATA)
        if rare and int(t * 2) % 2 == 0:  # blinking gold frame and star
            d.rectangle((0, 0, W - 1, H - 1), outline=GOLD)
            _star(d, W - 7, 7, 5, 2, GOLD)
        if offline:
            d.rectangle((W - 3, 0, W - 2, 1), fill=RED)
        return img

    def tracked(self, ac: Aircraft, trk: TrackInfo, cfg: dict, t: float = 0.0, offline: bool = False) -> Image.Image:
        """Followed-flight screen: same top rows, then a status row, alt/speed, and a progress bar on the bottom pixels."""
        img, d = self._new()
        img.paste(self.logo(ac, t), (1, 0))
        airline = airline_row(ac)
        ident = ac.callsign or trk.flight
        ident_route = f"{ident} {ac.route}" if ac.route else ident
        self._top_rows(img, airline, ident_route, type_row(ac), t)
        landed = trk.phase == "landed"
        self._text(img, (1, 46), track_line(ac, trk, cfg.get("units", {})), self.f5, GREEN if landed else WHITE, W - 2, t, speed=26.0)
        alt_spd, _ = data_lines(ac, cfg.get("units", {}))
        d.text((1, 54), alt_spd, font=self._fit(alt_spd, W - 2, [self.f5, self.f4]), fill=DATA)
        frac = 1.0 if landed else (trk.progress or 0.0)
        d.rectangle((0, 62, W - 1, 63), fill=(45, 45, 45))
        fill_w = int(round(frac * W))
        if fill_w:
            d.rectangle((0, 62, fill_w - 1, 63), fill=GREEN if landed else AMBER)
            if not landed:
                d.rectangle((max(0, fill_w - 2), 62, fill_w - 1, 63), fill=WHITE)  # bright head
        if offline:
            d.rectangle((W - 3, 0, W - 2, 1), fill=RED)
        return img

    def idle(self, cfg: dict, now: dt.datetime, t: float = 0.0, offline: bool = False,
             spotted: int = 0) -> Image.Image:
        img, d = self._new()
        hhmm = now.strftime("%H:%M")
        big = Image.new("RGB", (int(self.f6b.getlength(hhmm)), 12), (0, 0, 0))
        ImageDraw.Draw(big).text((0, 0), hhmm, font=self.f6b, fill=AMBER)
        big = big.resize((big.width * 2, big.height * 2), Image.NEAREST)
        img.paste(big, ((W - big.width) // 2, 6))
        date = now.strftime("%a %-d %b")
        d.text(((W - self.f5.getlength(date)) / 2, 34), date, font=self.f5, fill=WHITE)
        # bottom line rotates every 5 s: scanning / good morning-afternoon-evening-night / your greeting / spotted count
        hello = time_greeting(now, cfg.get("name", ""))
        pages = [("Scanning" + "." * (int(t * 2) % 4), DATA, "Scanning..."), (hello, GOLD, hello)]
        if (cfg.get("greeting") or "").strip():
            pages.append((cfg["greeting"].strip(), CYAN, cfg["greeting"].strip()))
        if spotted:
            pages.append((f"Spotted: {spotted:,}", AMBER, f"Spotted: {spotted:,}"))
        msg, col, widest = pages[int(t // 5) % len(pages)]
        if offline:
            msg, col, widest = "No connection", RED, "No connection"
        w = self.f5.getlength(widest)
        if w <= W - 2:
            d.text(((W - self.f5.getlength(msg if not msg.startswith("Scanning") else widest)) / 2, 50), msg, font=self.f5, fill=col)
        else:
            self._text(img, (1, 50), msg, self.f5, col, W - 2, t)
        return img

    def celebration(self, kind: str, text: str, t: float = 0.0) -> Image.Image:
        """Full-screen message with an original sprite: birthday cake, Christmas tree, or a starburst."""
        img, d = self._new()
        if kind == "xmas":
            _snow(d, t)
        draw_sprite = {"birthday": _cake, "xmas": _tree, "mothers": _flowers, "fathers": _tie}.get(kind, _burst)
        draw_sprite(d, 2, 4, t)
        if kind in ("milestone", "custom"):
            _confetti(d, t)
        lines, font = _wrap(text, W - 52, [self.f6, self.f5])
        y = max(4, (H - len(lines) * (font.size if hasattr(font, "size") else 12) - 4) // 2) if False else 8
        for ln in lines[:4]:
            d.text((50, y), ln, font=font, fill=GOLD if kind == "milestone" else WHITE)
            y += 13 if font is self.f6 else 10
        return img

    def test_pattern(self) -> Image.Image:
        """Border, corner labels, colour bars and a diagonal: wrong order, mirroring, or dead rows show up instantly."""
        img, d = self._new()
        d.rectangle((0, 0, W - 1, H - 1), outline=WHITE)
        for label, xy in (("TL", (3, 3)), ("TR", (W - 15, 3)), ("BL", (3, H - 10)), ("BR", (W - 15, H - 10))):
            d.text(xy, label, font=self.f5, fill=AMBER)
        bars = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
        for i, col in enumerate(bars):
            d.rectangle((20 + i * 22, 20, 40 + i * 22, 30), fill=col)
        for x in range(W):  # grey ramp
            v = int(255 * x / (W - 1))
            d.line((x, 36, x, 42), fill=(v, v, v))
        d.line((1, 1, H - 2, H - 2), fill=CYAN)
        d.text(((W - self.f5.getlength("128x64 TEST")) / 2, 48), "128x64 TEST", font=self.f5, fill=WHITE)
        return img

    def message(self, lines: list[str], colors: Optional[list] = None) -> Image.Image:
        img, d = self._new()
        colors = colors or [AMBER, WHITE, CYAN, DATA]
        y = 6
        for i, line in enumerate(lines[:5]):
            font = self._fit(line, W - 2, [self.f6, self.f5, self.f4])
            d.text(((W - font.getlength(line)) / 2, y), line, font=font, fill=colors[min(i, len(colors) - 1)])
            y += 12
        return img


def _heli_icon(size: int, heading: float, t: float) -> Image.Image:
    """Original top-down helicopter mask: body, tail boom, tail rotor, and a spinning two-blade main rotor.
    Drawn 4x then reduced for smooth edges. The body points along `heading`; the rotor spins with `t`."""
    s = size * 4
    u = s / 46
    c = s / 2
    body = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(body)
    d.ellipse((c - 6 * u, c - 9 * u, c + 6 * u, c + 8 * u), fill=255)                       # cabin
    d.polygon([(c - 1.6 * u, c + 6 * u), (c + 1.6 * u, c + 6 * u), (c + 1 * u, c + 21 * u), (c - 1 * u, c + 21 * u)], fill=255)  # tail boom
    d.polygon([(c - 4 * u, c + 19 * u), (c + 4 * u, c + 19 * u), (c + 4 * u, c + 21 * u), (c - 4 * u, c + 21 * u)], fill=255)    # tail fin
    d.line((c - 7 * u, c - 4 * u, c - 7 * u, c + 6 * u), fill=255, width=int(1.4 * u))     # skids
    d.line((c + 7 * u, c - 4 * u, c + 7 * u, c + 6 * u), fill=255, width=int(1.4 * u))
    body = body.rotate(-heading, resample=Image.BICUBIC, center=(c, c))
    rd = ImageDraw.Draw(body)
    phi = t * 14.0  # radians/s: fast enough to read as spinning at 15 fps without strobing into a still
    for k in (0, math.pi):
        a = phi + k
        rd.line((c, c, c + 21 * u * math.cos(a), c + 21 * u * math.sin(a)), fill=255, width=int(1.8 * u))
    rd.ellipse((c - 2 * u, c - 2 * u, c + 2 * u, c + 2 * u), fill=255)                      # hub
    return body.resize((size, size), Image.LANCZOS)


def _draw_icon(size: int, painter, heading: float, t: float, rotate: bool = True) -> Image.Image:
    """Draw an icon upright at 4x (nose up), rotate it to `heading`, and reduce for smooth edges."""
    s = size * 4
    u, c = s / 46, s / 2
    m = Image.new("L", (s, s), 0)
    painter(ImageDraw.Draw(m), u, c, t)
    if rotate:
        m = m.rotate(-heading, resample=Image.BICUBIC, center=(c, c))
    return m.resize((size, size), Image.LANCZOS)


def _paint_bizjet(d, u, c, t):  # slim body, small swept wings, rear engines, T-tail
    d.ellipse((c - 2 * u, 3 * u, c + 2 * u, 42 * u), fill=255)
    d.polygon([(c, 16 * u), (c + 14 * u, 29 * u), (c + 14 * u, 31.5 * u), (c, 26 * u), (c - 14 * u, 31.5 * u), (c - 14 * u, 29 * u)], fill=255)
    d.ellipse((c - 5.2 * u, 30 * u, c - 3 * u, 39 * u), fill=255)
    d.ellipse((c + 3 * u, 30 * u, c + 5.2 * u, 39 * u), fill=255)
    d.rectangle((c - 7 * u, 38 * u, c + 7 * u, 40 * u), fill=255)


def _paint_prop(d, u, c, t):  # straight wings, short body, spinning propeller
    d.ellipse((c - 2.6 * u, 9 * u, c + 2.6 * u, 42 * u), fill=255)
    d.rectangle((c - 17 * u, 19 * u, c + 17 * u, 25 * u), fill=255)
    d.rectangle((c - 8 * u, 37 * u, c + 8 * u, 40.5 * u), fill=255)
    blade = 1 * u + 5 * u * abs(math.cos(t * 9))  # half-length: the whole propeller swings between 2 and 12 px
    d.rectangle((c - blade, 7.2 * u, c + blade, 9 * u), fill=255)


def _paint_glider(d, u, c, t):  # very long thin wings, slim body
    d.ellipse((c - 1.4 * u, 4 * u, c + 1.4 * u, 42 * u), fill=255)
    d.polygon([(c - 22.5 * u, 20 * u), (c + 22.5 * u, 20 * u), (c + 22.5 * u, 22.5 * u), (c + 1.4 * u, 23.5 * u),
               (c - 1.4 * u, 23.5 * u), (c - 22.5 * u, 22.5 * u)], fill=255)
    d.rectangle((c - 5.5 * u, 38.5 * u, c + 5.5 * u, 40.3 * u), fill=255)
    d.ellipse((c - 2.2 * u, 10 * u, c + 2.2 * u, 19 * u), fill=255)


def _paint_fighter(d, u, c, t):  # sharp nose, swept delta wings, twin exhausts
    d.polygon([(c, 2 * u), (c + 2.2 * u, 12 * u), (c + 14 * u, 33 * u), (c + 14 * u, 37.5 * u), (c + 4.5 * u, 35 * u),
               (c + 3 * u, 42 * u), (c - 3 * u, 42 * u), (c - 4.5 * u, 35 * u), (c - 14 * u, 37.5 * u),
               (c - 14 * u, 33 * u), (c - 2.2 * u, 12 * u)], fill=255)
    d.line((c, 10 * u, c, 30 * u), fill=0, width=max(1, int(0.8 * u)))  # canopy seam keeps it from reading as a blob


def _paint_balloon(d, u, c, t):  # round envelope with panel seams, basket on ropes, gentle sway
    sw = math.sin(t * 1.5) * 0.8 * u
    d.ellipse((c - 14 * u + sw, 2 * u, c + 14 * u + sw, 30 * u), fill=255)
    d.polygon([(c - 6 * u + sw, 26 * u), (c + 6 * u + sw, 26 * u), (c + 2.5 * u, 34 * u), (c - 2.5 * u, 34 * u)], fill=255)
    d.rectangle((c - 3.2 * u, 36 * u, c + 3.2 * u, 42 * u), fill=255)
    for dx in (-8, -2.7, 2.7, 8):
        d.line((c + dx * u + sw, 3 * u, c + dx * u * 0.5 + sw * 0.5, 28 * u), fill=0, width=max(1, int(0.9 * u)))


def _paint_airship(d, u, c, t):  # long envelope, tail fins, side engine pods
    d.ellipse((c - 8 * u, 2 * u, c + 8 * u, 38 * u), fill=255)
    d.polygon([(c - 8 * u, 31 * u), (c - 13 * u, 41 * u), (c - 7 * u, 38 * u)], fill=255)
    d.polygon([(c + 8 * u, 31 * u), (c + 13 * u, 41 * u), (c + 7 * u, 38 * u)], fill=255)
    d.rectangle((c - 1 * u, 36 * u, c + 1 * u, 44 * u), fill=255)
    d.ellipse((c - 11 * u, 17 * u, c - 8 * u, 22 * u), fill=255)
    d.ellipse((c + 8 * u, 17 * u, c + 11 * u, 22 * u), fill=255)
    d.line((c, 6 * u, c, 34 * u), fill=0, width=max(1, int(0.7 * u)))


def _paint_drone(d, u, c, t):  # four arms, four spinning rotors
    for sx in (-1, 1):
        for sy in (-1, 1):
            cx, cy, r = c + sx * 12 * u, c + sy * 12 * u, 6.5 * u
            d.line((c, c, cx, cy), fill=255, width=max(1, int(1.8 * u)))
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=255, width=max(1, int(1.1 * u)))
            a = t * 18 + (sx + sy)
            d.line((cx - r * math.cos(a), cy - r * math.sin(a), cx + r * math.cos(a), cy + r * math.sin(a)), fill=255, width=max(1, int(0.9 * u)))
    d.rectangle((c - 4 * u, c - 4 * u, c + 4 * u, c + 4 * u), fill=255)


def _paint_vehicle(d, u, c, t):  # airport truck, seen from above
    d.rectangle((c - 8 * u, 8 * u, c + 8 * u, 18 * u), fill=255)
    d.rectangle((c - 7 * u, 19 * u, c + 7 * u, 40 * u), fill=255)


_PAINTERS = {"bizjet": _paint_bizjet, "prop": _paint_prop, "glider": _paint_glider, "fighter": _paint_fighter,
             "airship": _paint_airship, "drone": _paint_drone, "vehicle": _paint_vehicle}


def _icon_mask(kind: str, size: int, heading: float, t: float = 0.0) -> Image.Image:
    """L-mode silhouette for an aircraft kind, pointing along `heading` (balloons just sway)."""
    if kind == "helicopter":
        return _heli_icon(size, heading, t)
    if kind == "balloon":
        return _draw_icon(size, _paint_balloon, 0, t, rotate=False)
    if kind in _PAINTERS:
        return _draw_icon(size, _PAINTERS[kind], heading, t)
    return _plane_icon(size).rotate(-heading, resample=Image.BICUBIC)  # airliner and anything unknown


def _roundel(country: str, size: int) -> Optional[Image.Image]:
    """Simple drawn national military marking: GB, US, CA or DE. None for other countries."""
    if country not in ("GB", "US", "CA", "DE"):
        return None
    s = size * 4
    img = Image.new("RGB", (s, s), (0, 0, 0))
    d = ImageDraw.Draw(img)
    c, r = s / 2, s / 2 - 1
    blue, red = (25, 55, 150), (215, 35, 50)

    def disc(rr, col):
        d.ellipse((c - rr, c - rr, c + rr, c + rr), fill=col)

    if country == "GB":
        disc(r, blue); disc(r * 0.66, WHITE); disc(r * 0.33, red)
    elif country == "US":
        disc(r, blue)
        d.rectangle((c - r, c - r * 0.17, c - r * 0.6, c + r * 0.17), fill=WHITE)
        d.rectangle((c + r * 0.6, c - r * 0.17, c + r, c + r * 0.17), fill=WHITE)
        _star(d, c, c, r * 0.6, r * 0.25, WHITE)
    elif country == "CA":
        disc(r, blue); disc(r * 0.8, WHITE); _star(d, c, c, r * 0.62, r * 0.3, red, points=11)
    else:  # DE: black cross with a white edge
        d.rectangle((c - r * 0.92, c - r * 0.3, c + r * 0.92, c + r * 0.3), fill=WHITE)
        d.rectangle((c - r * 0.3, c - r * 0.92, c + r * 0.3, c + r * 0.92), fill=WHITE)
        d.rectangle((c - r * 0.82, c - r * 0.2, c + r * 0.82, c + r * 0.2), fill=(0, 0, 0))
        d.rectangle((c - r * 0.2, c - r * 0.82, c + r * 0.2, c + r * 0.82), fill=(0, 0, 0))
    return img.resize((size, size), Image.LANCZOS)


def _plane_icon(size: int) -> Image.Image:
    """White-on-black mask of a top-down airliner pointing up, drawn 4x then reduced for smooth edges."""
    s = size * 4
    m = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(m)
    c = s / 2
    u = s / 46
    d.ellipse((c - 2.5 * u, 3 * u, c + 2.5 * u, 43 * u), fill=255)                      # fuselage
    d.polygon([(c, 17 * u), (c + 21 * u, 29 * u), (c + 21 * u, 33 * u), (c, 26 * u),
               (c - 21 * u, 33 * u), (c - 21 * u, 29 * u)], fill=255)                   # wings
    d.polygon([(c, 36 * u), (c + 9 * u, 42 * u), (c + 9 * u, 44 * u), (c, 41 * u),
               (c - 9 * u, 44 * u), (c - 9 * u, 42 * u)], fill=255)                     # tailplane
    return m.resize((size, size), Image.LANCZOS)


def _star(d, cx, cy, r_out, r_in, fill, points=5):
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = -math.pi / 2 + i * math.pi / points
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    d.polygon(pts, fill=fill)


def _wrap(text: str, width: int, fonts):
    """Word-wrap `text` into lines no wider than `width` px; try each font, big to small, until it fits in 4 lines."""
    for f in fonts:
        lines, cur = [], ""
        for word in text.split():
            trial = (cur + " " + word).strip()
            if f.getlength(trial) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
        if len(lines) <= (4 if f is fonts[-1] else 3) or f is fonts[-1]:
            return lines, f
    return lines, fonts[-1]


def _cake(d, x, y, t):
    """Original pixel cake: two pink/cream layers, three candles with flickering flames."""
    d.rectangle((x + 4, y + 40, x + 40, y + 52), fill=(214, 90, 140))
    d.rectangle((x + 8, y + 28, x + 36, y + 40), fill=(255, 235, 200))
    d.rectangle((x + 8, y + 36, x + 36, y + 38), fill=(214, 90, 140))
    for i, cx in enumerate((x + 14, x + 22, x + 30)):
        d.rectangle((cx, y + 18, cx + 2, y + 28), fill=(80, 170, 255))
        h = 5 + ((int(t * 6) + i) % 3)
        d.rectangle((cx, y + 18 - h, cx + 2, y + 17), fill=(255, 190 + 20 * (i % 2), 40))
    d.rectangle((x, y + 52, x + 44, y + 54), fill=(200, 200, 210))


def _tree(d, x, y, t):
    """Original pixel tree with blinking baubles and a star."""
    for i, (top, half) in enumerate(((10, 10), (20, 15), (32, 20))):
        d.polygon([(x + 22, y + top - 8), (x + 22 - half, y + top + 12), (x + 22 + half, y + top + 12)], fill=(30, 150, 60))
    d.rectangle((x + 19, y + 44, x + 25, y + 52), fill=(120, 75, 30))
    _star(d, x + 22, y + 3, 5, 2, GOLD)
    for i, (bx, by) in enumerate(((16, 22), (28, 30), (14, 38), (30, 40), (22, 16))):
        on = (int(t * 2) + i) % 2 == 0
        d.rectangle((x + bx, y + by, x + bx + 2, y + by + 2), fill=RED if on else (255, 255, 255))


def _flowers(d, x, y, t):
    """Original pixel tulips in a bunch, swaying very slightly."""
    sway = int(round(math.sin(t * 2)))
    for cx, top, col in ((12, 12, (255, 90, 130)), (22, 6, (255, 205, 40)), (32, 14, (255, 130, 60))):
        d.line((x + 22, y + 52, x + cx + sway, y + top + 12), fill=(40, 160, 70), width=2)
        d.polygon([(x + cx - 6 + sway, y + top + 12), (x + cx - 6 + sway, y + top), (x + cx - 3 + sway, y + top + 4),
                   (x + cx + sway, y + top - 2), (x + cx + 3 + sway, y + top + 4), (x + cx + 6 + sway, y + top),
                   (x + cx + 6 + sway, y + top + 12)], fill=col)
    d.polygon([(x + 22, y + 52), (x + 8, y + 40), (x + 14, y + 52)], fill=(30, 130, 60))
    d.polygon([(x + 22, y + 52), (x + 36, y + 40), (x + 30, y + 52)], fill=(30, 130, 60))
    d.rectangle((x + 14, y + 50, x + 30, y + 55), fill=(200, 120, 60))


def _tie(d, x, y, t):
    """Original pixel necktie with diagonal stripes and a little shimmer."""
    d.polygon([(x + 16, y + 2), (x + 28, y + 2), (x + 26, y + 12), (x + 18, y + 12)], fill=(30, 70, 170))
    d.polygon([(x + 18, y + 12), (x + 26, y + 12), (x + 34, y + 46), (x + 22, y + 56), (x + 10, y + 46)], fill=(40, 90, 200))
    for k in range(5):
        yy = y + 18 + k * 8
        d.line((x + 14 + (k % 2), yy + 6, x + 30 - (k % 2), yy), fill=(235, 235, 245), width=1)
    d.point((x + 22 + int(3 * math.sin(t * 3)), y + 30), fill=WHITE)


def _snow(d, t):
    for i in range(22):
        sx = (i * 37) % W
        sy = int((t * (6 + i % 5) + i * 11) % H)
        d.point((sx, sy), fill=(200, 215, 235))


def _burst(d, x, y, t):
    """Original starburst that pulses."""
    r = 20 + int(3 * math.sin(t * 5))
    _star(d, x + 22, y + 27, r, r // 2, GOLD, points=8)
    _star(d, x + 22, y + 27, r // 2, r // 4, (255, 120, 40), points=8)


def _confetti(d, t):
    cols = [(255, 80, 80), (80, 200, 255), (120, 255, 120), (255, 220, 60), (255, 120, 220)]
    for i in range(18):
        cx = (i * 53 + 7) % W
        cy = int((t * (8 + i % 4) + i * 13) % H)
        d.rectangle((cx, cy, cx + 1, cy + 1), fill=cols[i % len(cols)])


def slide(prev: Image.Image, nxt: Image.Image, progress: float) -> Image.Image:
    """Horizontal push transition; progress 0 -> prev, 1 -> nxt (eased)."""
    p = max(0.0, min(1.0, progress))
    p = p * p * (3 - 2 * p)
    off = int(round(W * p))
    out = Image.new("RGB", (W, H))
    out.paste(prev, (-off, 0))
    out.paste(nxt, (W - off, 0))
    return out


_dot_masks: dict = {}


def led_look(img: Image.Image, scale: int = 8) -> Image.Image:
    """Upscale with round dots and dark gaps so it reads like the real panel."""
    if scale not in _dot_masks:
        cell = Image.new("L", (scale, scale), 0)
        ImageDraw.Draw(cell).ellipse((0, 0, scale - 1, scale - 1), fill=255)
        mask = Image.new("L", (W * scale, H * scale), 0)
        for y in range(H):
            for x in range(W):
                mask.paste(cell, (x * scale, y * scale))
        _dot_masks[scale] = mask  # built once, ~0.1s
    big = img.resize((W * scale, H * scale), Image.NEAREST)
    return Image.composite(big, Image.new("RGB", big.size, (8, 8, 8)), _dot_masks[scale])
