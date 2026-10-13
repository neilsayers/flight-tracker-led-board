import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path

from flightboard import config, data, easter, tracker
from flightboard.aircraft_types import friendly
from flightboard.clock import effective_brightness
from flightboard.render import Renderer, eta_text, fmt_alt, fmt_speed, fmt_vrate, route_line, short_airline


class Formatting(unittest.TestCase):
    def test_alt(self):
        self.assertEqual(fmt_alt(4100), "4.1k ft")
        self.assertEqual(fmt_alt(36000), "36k ft")
        self.assertEqual(fmt_alt(850), "850 ft")
        self.assertEqual(fmt_alt(None), "--")
        self.assertEqual(fmt_alt(0, on_ground=True), "ground")
        self.assertEqual(fmt_alt(3281, "m"), "1.0k m")

    def test_speed_vrate(self):
        self.assertEqual(fmt_speed(217, "mph"), "250mph")
        self.assertEqual(fmt_speed(100, "kt"), "100kt")
        self.assertEqual(fmt_vrate(-1080), "-18ft/s")

    def test_names(self):
        self.assertEqual(short_airline("United Airlines"), "United")
        self.assertEqual(short_airline("Virgin Atlantic Airways Limited"), "Virgin Atlantic")
        self.assertEqual(short_airline("Airways"), "Airways")
        self.assertEqual(friendly("A388"), "Airbus A380")
        self.assertEqual(friendly("ZZZZ"), "ZZZZ")
        self.assertEqual(data.airport_name({"name": "Belfast International Airport", "municipality": "Belfast"}), "Belfast International")
        self.assertEqual(data.airport_name({"name": "Charles de Gaulle International Airport", "municipality": "Paris"}), "Paris Charles de Gaulle")


class Data(unittest.TestCase):
    def test_parse_and_pick(self):
        raw = [{"hex": "AA", "lat": 51.5, "lon": -0.1, "alt_baro": "ground", "flight": "X1 "},
               {"hex": "BB", "lat": 51.6, "lon": -0.1, "alt_baro": 3000, "gs": 200.0, "flight": "BAW1 "},
               {"hex": "CC", "lat": 51.9, "lon": -0.1, "alt_baro": 9000},
               {"hex": "DD"}]
        acs = [a for a in (data.parse_aircraft(r, 51.5, -0.1) for r in raw) if a]
        self.assertEqual(len(acs), 3)  # no-position entry dropped
        self.assertEqual(data.pick_nearest(acs).hex, "bb")  # nearest airborne, ground one skipped
        self.assertEqual(data.pick_nearest(acs, include_ground=True).hex, "aa")
        self.assertEqual(acs[1].callsign, "BAW1")
        self.assertEqual(acs[1].airline_prefix, "BAW")
        self.assertIsNone(data.pick_nearest([]))

    def test_parse_route(self):
        r = data.parse_route({"response": {"flightroute": {"airline": {"name": "X", "icao": "XXX"},
            "origin": {"iata_code": "LHR", "name": "London Heathrow Airport", "municipality": "London"},
            "destination": {"iata_code": "CDG", "name": "Charles de Gaulle International Airport", "municipality": "Paris",
                            "latitude": 49.0, "longitude": 2.5}}}})
        self.assertEqual((r["origin"], r["destination"], r["origin_name"]), ("LHR", "CDG", "London Heathrow"))
        self.assertEqual(data.parse_route({"response": "unknown callsign"}), {})
        self.assertEqual(data.parse_route(None), {})


class Choosing(unittest.TestCase):
    def setUp(self):
        import threading
        from flightboard import display
        self.f = display.Fetcher(display.State(), lambda: {}, threading.Event())
        self.pool = [data.Aircraft(hex=h, distance_nm=d) for h, d in (("a", 2.0), ("b", 5.0), ("c", 9.0))]

    def test_rotate_cycles_through_everyone_then_repeats(self):
        cfg, cur, seq = {"mode": "rotate", "dwell_secs": 15, "rotate_count": 6}, None, []
        for i in range(7):
            cur = self.f._choose(self.pool, cur, cfg, 1000.0 + i * 16)
            seq.append(cur.hex)
        self.assertEqual(seq, ["a", "b", "c", "a", "b", "c", "a"])

    def test_rotate_holds_for_dwell_time(self):
        cfg = {"mode": "rotate", "dwell_secs": 15, "rotate_count": 6}
        cur = self.f._choose(self.pool, None, cfg, 1000.0)
        self.assertEqual(self.f._choose(self.pool, cur, cfg, 1010.0).hex, cur.hex)   # 10 s: still the same
        self.assertNotEqual(self.f._choose(self.pool, cur, cfg, 1016.0).hex, cur.hex)  # 16 s: moved on

    def test_rotate_leaves_a_plane_that_dropped_out(self):
        cfg = {"mode": "rotate", "dwell_secs": 15, "rotate_count": 6}
        cur = self.f._choose(self.pool, None, cfg, 1000.0)
        gone = [a for a in self.pool if a.hex != cur.hex]
        self.assertNotEqual(self.f._choose(gone, cur, cfg, 1001.0).hex, cur.hex)

    def test_nearest_mode_is_sticky_but_switches_when_clearly_closer(self):
        cfg = {"mode": "nearest"}
        cur = self.pool[1]  # showing b at 5.0
        self.pool[1].distance_nm = 2.3
        self.assertEqual(self.f._choose(sorted(self.pool, key=lambda a: a.distance_nm), cur, cfg, 1.0).hex, "b")  # a is only 0.3 closer
        self.pool[1].distance_nm = 5.0
        self.assertEqual(self.f._choose(self.pool, cur, cfg, 1.0).hex, "a")

    def test_stale_positions_are_dropped_and_adsbfi_key_is_read(self):
        old, new = {"hex": "a", "lat": 51.5, "lon": 0.1, "seen_pos": 90.0}, {"hex": "b", "lat": 51.5, "lon": 0.1, "seen_pos": 1.0}
        orig = data._get_json
        data._get_json = lambda url: {"aircraft": [old, new]}  # adsb.fi's list key
        try:
            got = data.fetch_nearby(51.5, 0.1, 10)
        finally:
            data._get_json = orig
        self.assertEqual([a.hex for a in got], ["b"])


class SimulatorServer(unittest.TestCase):
    def test_serves_page_and_frames_and_can_listen_on_any_interface(self):
        import urllib.request
        from flightboard import display
        from PIL import Image
        out = display.SimOutput(0, "127.0.0.1")            # port 0: let the OS pick a free one
        try:
            base = f"http://127.0.0.1:{out.server.server_address[1]}"
            self.assertIn(b"Flight Board simulator", urllib.request.urlopen(base + "/").read())
            out.show(Image.new("RGB", (128, 64), (255, 0, 0)), 100)
            png = urllib.request.urlopen(base + "/frame.png?dots=0").read()
            self.assertEqual(png[:4], b"\x89PNG")
        finally:
            out.close()
        wide = display.SimOutput(0, "0.0.0.0")             # what --host 0.0.0.0 does: reachable from other machines
        try:
            self.assertEqual(wide.server.server_address[0], "0.0.0.0")
        finally:
            wide.close()


class Rotation(unittest.TestCase):
    """The between-polls tick that makes the dwell time accurate."""

    def setUp(self):
        import threading
        from flightboard import display
        self.cfg = {"mode": "rotate", "dwell_secs": 10, "rotate_count": 6}
        self.f = display.Fetcher(display.State(), lambda: self.cfg, threading.Event())
        self.f.routes.enrich = lambda ac: ac            # no network in tests
        self.f.aircraft_info.enrich = lambda ac: ac
        self.f.pool = [data.Aircraft(hex=h, distance_nm=d) for h, d in (("a", 2.0), ("b", 5.0), ("c", 9.0))]
        self.f.pool_time = 1000.0

    def shown(self):
        return self.f.state.aircraft.hex

    def test_switches_after_the_dwell_time_not_the_poll_time(self):
        self.f._tick(now=1000.0)
        self.assertEqual(self.shown(), "a")
        self.f._tick(now=1009.0)
        self.assertEqual(self.shown(), "a")            # 9 s: still on it
        self.f._tick(now=1010.0)
        self.assertEqual(self.shown(), "b")            # 10 s: moved on, between polls
        self.f._tick(now=1020.0)
        self.assertEqual(self.shown(), "c")

    def test_each_speed_setting(self):
        for dwell in (10, 20, 30):
            self.setUp()
            self.cfg["dwell_secs"] = dwell
            self.f._tick(now=1000.0)
            self.f._tick(now=1000.0 + dwell - 1)
            self.assertEqual(self.shown(), "a")
            self.f._tick(now=1000.0 + dwell)
            self.assertEqual(self.shown(), "b")

    def test_does_not_rotate_from_stale_data_or_in_other_modes(self):
        self.f._tick(now=1000.0)
        self.f._tick(now=1100.0)                       # pool is 100 s old: the API has been failing
        self.assertEqual(self.shown(), "a")
        self.cfg["mode"] = "nearest"
        self.f.pool_time = 1100.0
        self.f._tick(now=1111.0)
        self.assertEqual(self.shown(), "a")
        self.cfg.update(mode="rotate", track={"flight": "BA11"})
        self.f._tick(now=1111.0)                       # tracking a flight: leave the display alone
        self.assertEqual(self.shown(), "a")


class AircraftNames(unittest.TestCase):
    def test_readable_names_from_adsbdb(self):
        info = data.parse_aircraft_info({"response": {"aircraft": {"type": "Citation CJ4", "manufacturer": "Cessna",
                                         "registered_owner": "Jetkontor", "registration": "D-COLO"}}})
        self.assertEqual(info["type_full"], "Cessna Citation CJ4")
        ac = data.Aircraft(hex="x", type_code="C25C", type_full=info["type_full"])
        self.assertEqual(ac.type_name, "Cessna Citation CJ4")
        shouty = data.parse_aircraft_info({"response": {"aircraft": {"type": "R44", "manufacturer": "ROBINSON HELICOPTER"}}})
        self.assertEqual(shouty["type_full"], "Robinson Helicopter R44")
        dup = data.parse_aircraft_info({"response": {"aircraft": {"type": "Cessna 172", "manufacturer": "Cessna"}}})
        self.assertEqual(dup["type_full"], "Cessna 172")  # no "Cessna Cessna 172"
        self.assertEqual(data.parse_aircraft_info({"response": "unknown aircraft"}), {})
        loud = data.parse_aircraft_info({"response": {"aircraft": {"type": "X", "registered_owner": "WESTGATE AVIATION GV LLC"}}})
        self.assertEqual(loud["owner"], "Westgate Aviation GV LLC")
        self.assertEqual(data.parse_aircraft_info({"response": {"aircraft": {"type": "X", "registered_owner": "Jetkontor"}}})["owner"], "Jetkontor")
        self.assertEqual(data.Aircraft(hex="v", callsign="VJT409").operator, "VistaJet")
        self.assertEqual(data.Aircraft(hex="v", callsign="VJT409", airline="British Airways").operator, "British Airways")
        self.assertEqual(data.Aircraft(hex="v", type_code="CL35").type_name, "Bombardier Challenger 350")
        # the curated name still wins for common airliners
        self.assertEqual(data.Aircraft(hex="y", type_code="A21N", type_full="Airbus A321-271NX").type_name, "Airbus A321neo")


class OddText(unittest.TestCase):
    """Names from the data feeds can contain characters the bitmap font lacks. They must never crash a frame."""
    NASTY = ["Bucharest Henri Coandă", "Timișoara Traian Vuia", "Łódź Władysław Reymont", "Zürich", "Reykjavík Keflavík",
             "東京 羽田", "Москва", "Hi Zoë 😀", "Ωmega Air", "Ŵelsh Ŷ"]

    def test_safe_text(self):
        from flightboard.render import safe_text
        self.assertEqual(safe_text("Coandă"), "Coanda")
        self.assertEqual(safe_text("Łódź"), "Lódz")  # Ł and ź fold; ó is Latin-1 and stays
        self.assertEqual(safe_text("Zürich"), "Zürich")        # Latin-1 accents survive
        self.assertEqual(safe_text("東京"), "??")
        self.assertTrue(all(ord(c) < 256 for c in safe_text("".join(self.NASTY))))

    def test_every_screen_survives_odd_text(self):
        from flightboard.render import Renderer
        r, cfg = Renderer(), dict(config.DEFAULTS, greeting="Hi Zoë 😀", name="Zoë")
        for name in self.NASTY:
            ac = data.Aircraft(hex="z", callsign="XYZ1", type_code="ZZZZ", type_full=name, owner=name, airline=name,
                               origin="AAA", destination="BBB", origin_name=name, destination_name=name,
                               dest_lat=45.0, dest_lon=20.0, lat=50.0, lon=0.0, gs_kt=400, alt_ft=30000, registration=name)
            for t in (0.0, 3.0, 9.0):
                self.assertEqual(r.aircraft(ac, cfg, t=t, rare=name).size, (128, 64))
                trk = data.TrackInfo(flight="XY1", phase="cruising", progress=0.5)
                self.assertEqual(r.tracked(ac, trk, cfg, t=t).size, (128, 64))
            self.assertEqual(r.message([name, name]).size, (128, 64))
            self.assertEqual(r.celebration("birthday", name, 1.0).size, (128, 64))
        self.assertEqual(r.idle(cfg, dt.datetime(2026, 1, 1, 12, 0), t=7).size, (128, 64))


class Kinds(unittest.TestCase):
    def kind(self, **kw):
        return data.Aircraft(hex="x", **kw).kind

    def test_merlin_and_other_military_helicopters_are_helicopters(self):
        self.assertEqual(self.kind(type_code="EH10"), "helicopter")                       # no category broadcast
        self.assertEqual(self.kind(type_full="AgustaWestland EH-101 Merlin HM2"), "helicopter")  # recognised from its name
        self.assertEqual(self.kind(type_code="H60", military=True), "helicopter")
        self.assertEqual(self.kind(category="A7"), "helicopter")

    def test_each_kind(self):
        self.assertEqual(self.kind(type_code="GLID", category="A1"), "glider")             # type beats the 'light aircraft' category
        self.assertEqual(self.kind(category="B1"), "glider")
        self.assertEqual(self.kind(category="B2", gs_kt=8), "balloon")
        self.assertEqual(self.kind(category="B2", gs_kt=55), "airship")
        self.assertEqual(self.kind(type_full="Zeppelin NT airship", category="B2"), "airship")
        self.assertEqual(self.kind(category="B6"), "drone")
        self.assertEqual(self.kind(type_code="EUFI", military=True), "fighter")
        self.assertEqual(self.kind(category="A6", military=True), "fighter")
        self.assertEqual(self.kind(category="A6"), "airliner")                              # fast but not military: not a fighter
        self.assertEqual(self.kind(type_code="C25C"), "bizjet")
        self.assertEqual(self.kind(callsign="VJT409", type_code="XXXX"), "bizjet")
        self.assertEqual(self.kind(type_code="C172", category="A1"), "prop")
        self.assertEqual(self.kind(type_code="A320", category="A3"), "airliner")
        self.assertEqual(self.kind(type_code="C17", military=True), "airliner")             # transports use the airliner shape

    def test_military_detection(self):
        mil = lambda **kw: data.Aircraft(hex="x", **kw).is_military
        self.assertTrue(mil(military=True))
        self.assertTrue(mil(callsign="ASCOT4789"))
        self.assertTrue(mil(owner="Royal Navy"))
        self.assertTrue(mil(type_code="EUFI"))
        self.assertFalse(mil(type_code="A320", callsign="BAW1"))
        self.assertFalse(mil(owner="National Police Air Service"))

    def test_airline_row_and_type_row(self):
        from flightboard.render import airline_row, type_row
        self.assertEqual(airline_row(data.Aircraft(hex="x", owner="Royal Navy")), "Royal Navy (Military)")
        self.assertEqual(airline_row(data.Aircraft(hex="x", military=True)), "Military")
        self.assertEqual(airline_row(data.Aircraft(hex="x", airline="British Airways")), "British")  # shortened on purpose
        self.assertEqual(airline_row(data.Aircraft(hex="x", registration="G-HOTA")), "Private flight")
        self.assertEqual(type_row(data.Aircraft(hex="x", category="B2", registration="G-HOTA")), "Balloon")
        self.assertEqual(type_row(data.Aircraft(hex="x", type_code="GLID")), "Glider")
        self.assertEqual(type_row(data.Aircraft(hex="x", type_code="A320")), "Airbus A320")

    def test_icons_are_distinct_and_point_the_right_way(self):
        from flightboard.render import _icon_mask
        kinds = ["airliner", "bizjet", "prop", "glider", "fighter", "helicopter", "balloon", "airship", "drone", "vehicle"]
        masks = {k: _icon_mask(k, 46, 0, 0.3).tobytes() for k in kinds}
        self.assertEqual(len(set(masks.values())), len(kinds))                              # every kind looks different
        self.assertNotEqual(_icon_mask("fighter", 46, 0).tobytes(), _icon_mask("fighter", 46, 90).tobytes())
        self.assertEqual(_icon_mask("balloon", 46, 0, 0.3).tobytes(), _icon_mask("balloon", 46, 90, 0.3).tobytes())  # balloons don't rotate

    def test_propeller_never_longer_than_12_px(self):
        from flightboard.render import _icon_mask
        widest = 0
        for i in range(60):  # a full spin cycle at 15 fps
            m = _icon_mask("prop", 46, 0, i / 15).convert("L")
            for y in (6, 7, 8):  # the rows the propeller sits in, above the nose
                widest = max(widest, sum(1 for x in range(46) if m.getpixel((x, y)) > 100))
        self.assertLessEqual(widest, 13)   # 12 px plus a pixel of edge smoothing
        self.assertGreaterEqual(widest, 9)  # and it really does swing out to about full length

    def test_military_badge_is_framed_and_does_not_cover_the_icon(self):
        from flightboard.render import BADGE_BOX, BADGE_FRAME, LOGO_SIZE, Renderer
        tile = Renderer().logo(data.Aircraft(hex="43c001", type_code="EUFI", military=True, track=0), 0.0)
        x = LOGO_SIZE - BADGE_BOX
        self.assertEqual(tile.getpixel((x, x)), BADGE_FRAME)                      # grey outline corner
        self.assertEqual(tile.getpixel((LOGO_SIZE - 1, LOGO_SIZE - 1)), BADGE_FRAME)
        self.assertEqual(tile.getpixel((x - 1, LOGO_SIZE - 1)), (0, 0, 0))        # no black slab spilling outside the frame

    def test_countries_and_military_badges(self):
        from flightboard.countries import country_of
        from flightboard.render import Renderer, _roundel
        self.assertEqual([country_of(h) for h in ("43c001", "ae0001", "c00001", "3f0001", "ffffff", "zz")], ["GB", "US", "CA", "DE", "", ""])
        self.assertIsNotNone(_roundel("GB", 20))
        self.assertIsNone(_roundel("", 20))
        r = Renderer()
        gb = data.Aircraft(hex="43c001", type_code="EUFI", military=True, track=90)       # no emblem in the collection: roundel
        from flightboard.render import BADGE
        self.assertEqual(r.military_emblem(gb).size, (BADGE, BADGE))
        unknown = data.Aircraft(hex="123456", type_code="EUFI", military=True)            # unknown country: silhouette only
        self.assertIsNone(r.military_emblem(unknown))
        self.assertEqual(r.logo(unknown, 0.0).size, (46, 46))
        self.assertEqual(r.logo(gb, 0.0).size, (46, 46))


class Roles(unittest.TestCase):
    def test_role_labels(self):
        from flightboard.aircraft_types import role_label
        self.assertEqual(role_label("National Police Air Service"), "Police")
        self.assertEqual(role_label("Police Service of Northern Ireland"), "Police")
        self.assertEqual(role_label("Maritime and Coastguard Agency"), "Coastguard")
        self.assertEqual(role_label("Great North Air Ambulance Service"), "Air Ambulance")
        self.assertEqual(role_label("Ministry of Defence"), "Royal Air Force")
        self.assertEqual(role_label("Jetkontor"), "")
        self.assertEqual(role_label("Firefly Aviation"), "")  # 'fire' must be a whole word

    def test_operator_order(self):
        heli = data.Aircraft(hex="h", owner="National Police Air Service")
        self.assertEqual(heli.operator, "Police")
        self.assertEqual(data.Aircraft(hex="h", owner="Jetkontor").operator, "Jetkontor")
        self.assertEqual(data.Aircraft(hex="h", military=True).operator, "Military")
        self.assertEqual(data.Aircraft(hex="h", airline="British Airways", owner="Police").operator, "British Airways")

    def test_helicopter_detection_and_parse(self):
        self.assertTrue(data.Aircraft(hex="h", category="A7").is_helicopter)
        self.assertTrue(data.Aircraft(hex="h", type_code="EC45").is_helicopter)
        self.assertFalse(data.Aircraft(hex="h", type_code="A320", category="A3").is_helicopter)
        a = data.parse_aircraft({"hex": "x", "lat": 1.0, "lon": 1.0, "category": "A7", "dbFlags": 1}, 1.0, 1.0)
        self.assertEqual((a.category, a.military), ("A7", True))

    def test_helicopter_icon_spins_and_frames_render(self):
        from flightboard.render import Renderer, _heli_icon
        self.assertNotEqual(_heli_icon(46, 90, 0.0).tobytes(), _heli_icon(46, 90, 0.1).tobytes())  # rotor moved
        self.assertNotEqual(_heli_icon(46, 0, 0.0).tobytes(), _heli_icon(46, 180, 0.0).tobytes())  # heading rotates it
        r = Renderer()
        for key in ("police_helicopter", "coastguard_helicopter"):
            from flightboard.samples import SAMPLES
            self.assertEqual(r.aircraft(data.Aircraft(**SAMPLES[key]), config.DEFAULTS, t=1.0).size, (128, 64))

    def test_route_line_falls_back_to_owner(self):
        from flightboard.render import route_line
        ac = data.Aircraft(hex="h", owner="National Police Air Service", registration="G-MPSC")
        self.assertEqual(route_line(ac, dt.datetime(2026, 1, 1)), "National Police Air Service (G-MPSC)")


class Places(unittest.TestCase):
    def test_postcode_vs_place_routing(self):
        calls = []
        lp, sp = data.lookup_postcode, data.search_places
        data.lookup_postcode = lambda q: calls.append(("pc", q)) or (51.0, -1.0)
        data.search_places = lambda q, limit=5: calls.append(("place", q)) or [{"name": "Omagh, NI", "lat": 54.6, "lon": -7.3}]
        try:
            self.assertEqual(data.geocode("ba21 4aa")["name"], "BA21 4AA")
            self.assertEqual(data.geocode("Omagh")["name"], "Omagh, NI")
            self.assertIsNone(data.geocode("   "))
            self.assertEqual([c[0] for c in calls], ["pc", "place"])
        finally:
            data.lookup_postcode, data.search_places = lp, sp


class Rendering(unittest.TestCase):
    def test_eta_and_route_line(self):
        now = dt.datetime(2026, 1, 1, 12, 0)
        ac = data.Aircraft(hex="a", lat=51.0, lon=0.0, gs_kt=400, origin_name="London Heathrow",
                           destination_name="Paris", dest_lat=49.0, dest_lon=2.5)
        self.assertRegex(eta_text(ac, now), r"^12:\d\d$")
        self.assertIn("(ETA: 12:", route_line(ac, now))
        ac.gs_kt = 50
        self.assertEqual(eta_text(ac, now), "")
        self.assertEqual(route_line(data.Aircraft(hex="b"), now), "Route not available")

    def test_frames_are_128x64_for_all_states(self):
        r, cfg = Renderer(), config.DEFAULTS
        from flightboard.samples import SAMPLES
        for kw in SAMPLES.values():
            self.assertEqual(r.aircraft(data.Aircraft(**kw), cfg, t=3.0).size, (128, 64))
        self.assertEqual(r.idle(cfg, dt.datetime.now()).size, (128, 64))
        self.assertEqual(r.message(["a", "b"]).size, (128, 64))


class Easter(unittest.TestCase):
    def test_rarity(self):
        cfg = config.DEFAULTS
        self.assertEqual(easter.rarity(data.Aircraft(hex="a", type_code="A388"), cfg), "Airbus A380")
        self.assertEqual(easter.rarity(data.Aircraft(hex="a", callsign="RRR3"), cfg), "Red Arrows")
        self.assertIsNone(easter.rarity(data.Aircraft(hex="a", type_code="A320", callsign="EZY1"), cfg))
        off = {"easter": {"rare_alerts": False}}
        self.assertIsNone(easter.rarity(data.Aircraft(hex="a", type_code="A388"), off))
        mine = {"easter": {"rare_alerts": True, "watch_types": ["pc12"], "watch_callsigns": ["GAF"]}}
        self.assertIsNotNone(easter.rarity(data.Aircraft(hex="a", type_code="PC12"), mine))
        self.assertEqual(easter.rarity(data.Aircraft(hex="a", callsign="GAF123"), mine), "GAF")

    def test_time_greeting(self):
        at = lambda h, m=0: dt.datetime(2026, 1, 1, h, m)
        self.assertEqual(easter.time_greeting(at(4, 59)), "Good night")
        self.assertEqual(easter.time_greeting(at(5, 0)), "Good morning")
        self.assertEqual(easter.time_greeting(at(11, 59)), "Good morning")
        self.assertEqual(easter.time_greeting(at(12, 0), "Sam"), "Good afternoon, Sam")
        self.assertEqual(easter.time_greeting(at(17, 59)), "Good afternoon")
        self.assertEqual(easter.time_greeting(at(18, 0)), "Good evening")
        self.assertEqual(easter.time_greeting(at(21, 59)), "Good evening")
        self.assertEqual(easter.time_greeting(at(22, 0), " "), "Good night")

    def test_milestones(self):
        hits = [n for n in range(1, 3001) if easter.is_milestone(n)]
        self.assertEqual(hits, [50, 100, 500, 1000, 2000, 3000])

    def test_mothers_and_fathers_day(self):
        # known UK dates
        self.assertEqual(easter.fathers_day(2026), dt.date(2026, 6, 21))
        self.assertEqual(easter.fathers_day(2027), dt.date(2027, 6, 20))
        self.assertEqual(easter.fathers_day(2028), dt.date(2028, 6, 18))
        self.assertEqual(easter.easter_sunday(2026), dt.date(2026, 4, 5))
        self.assertEqual(easter.mothers_day(2026), dt.date(2026, 3, 15))
        self.assertEqual(easter.mothers_day(2027), dt.date(2027, 3, 7))
        self.assertEqual(easter.mothers_day(2025), dt.date(2025, 3, 30))
        for y in range(2026, 2060):  # always a Sunday, always in the right season
            self.assertEqual(easter.fathers_day(y).weekday(), 6)
            self.assertTrue(15 <= easter.fathers_day(y).day <= 21)
            self.assertEqual(easter.mothers_day(y).weekday(), 6)
        self.assertEqual(easter.active_event({"name": "Sam"}, dt.date(2026, 6, 21)), ("fathers", "Happy Father's Day, Sam!"))
        self.assertEqual(easter.active_event({}, dt.date(2026, 3, 15)), ("mothers", "Happy Mother's Day!"))

    def test_events(self):
        cfg = {"name": "Sam", "events": [{"date": "03-14", "text": "Happy Birthday Sam!", "kind": "birthday"}]}
        self.assertEqual(easter.active_event(cfg, dt.date(2026, 3, 14)), ("birthday", "Happy Birthday Sam!"))
        self.assertEqual(easter.active_event(cfg, dt.date(2026, 12, 25)), ("xmas", "Merry Christmas, Sam!"))
        self.assertIsNone(easter.active_event(cfg, dt.date(2026, 7, 4)))
        self.assertEqual(easter.parse_events("03-14 Happy Birthday Sam!\nbad line\n13-45 nope")[0]["kind"], "birthday")
        self.assertEqual(len(easter.parse_events("03-14 Happy Birthday Sam!\nbad line\n13-45 nope")), 1)

    def test_spotlog_dedupes_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            log = easter.SpotLog(p)
            log.count = 49
            self.assertEqual(log.record("abc", 1000.0), (50, True))
            self.assertEqual(log.record("abc", 1500.0), (50, False))  # same plane, same sitting
            self.assertEqual(easter.SpotLog(p).count, 50)             # survived a restart
            self.assertEqual(log.record("abc", 1000.0 + 7 * 3600), (51, False))  # seen again hours later


class Tracking(unittest.TestCase):
    ROUTE = {"callsign_icao": "BAW11", "airline": "British Airways", "airline_icao": "BAW", "origin": "LHR", "destination": "CDG",
             "origin_name": "London Heathrow", "destination_name": "Paris Charles de Gaulle",
             "origin_lat": 51.47, "origin_lon": -0.46, "dest_lat": 49.01, "dest_lon": 2.55}

    def setUp(self):
        self._lf, self._fc = data.lookup_flight, data.fetch_callsign
        data.lookup_flight = lambda f: dict(self.ROUTE)
        self.sky = []
        data.fetch_callsign = lambda cs: self.sky
        self.tr = tracker.Tracker()
        self.t0 = dt.datetime(2026, 10, 12, 18, 0)
        self.started = self.t0.timestamp() - 600  # tracking began 10 minutes before the first poll

    def tearDown(self):
        data.lookup_flight, data.fetch_callsign = self._lf, self._fc

    def plane(self, lat, lon, alt=30000, gs=450, ground=False, vr=0):
        return data.Aircraft(hex="abc", callsign="BAW11", lat=lat, lon=lon, alt_ft=0 if ground else alt, gs_kt=gs,
                             on_ground=ground, vrate_fpm=vr)

    def test_full_flight(self):
        step = lambda m: self.tr.step("BA11", self.started, self.t0 + dt.timedelta(minutes=m))
        ac, info, ok = step(0)                       # not airborne yet
        self.assertEqual((info.phase, ok), ("waiting", True))
        self.assertEqual(ac.origin_name, "London Heathrow")
        self.sky = [self.plane(51.4, -0.5, alt=3000, vr=2500)]  # just took off
        ac, info, _ = step(10)
        self.assertEqual(info.phase, "climbing")
        self.assertEqual(info.departed_at, self.t0 + dt.timedelta(minutes=10))
        self.assertLess(info.progress, 0.1)
        self.sky = [self.plane(50.2, 1.0)]           # cruising, mid-way
        ac, info, _ = step(30)
        self.assertEqual(info.phase, "cruising")
        self.assertTrue(0.3 < info.progress < 0.8)
        self.assertGreater(info.eta, self.t0)
        self.sky = [self.plane(49.2, 2.2, alt=4000, gs=200, vr=-1500)]
        ac, info, _ = step(50)
        self.assertEqual(info.phase, "descending")
        self.sky = []                                # vanishes near the destination
        _, info, _ = step(52)
        self.assertNotEqual(info.phase, "landed")    # grace period first
        ac, info, _ = step(56)
        self.assertEqual((info.phase, info.progress), ("landed", 1.0))
        self.assertEqual(info.landed_at, self.t0 + dt.timedelta(minutes=50))  # last time it was seen
        self.assertTrue(ac.on_ground)
        ac, info, _ = step(60)
        self.assertEqual(info.phase, "landed")
        ac, info, _ = step(50 + 25)                  # 20 minute hold is over
        self.assertIsNone(info)

    def test_started_mid_flight_has_no_departure_time(self):
        self.sky = [self.plane(50.0, 1.5)]
        _, info, _ = self.tr.step("BA11", self.started, self.t0)
        self.assertIsNone(info.departed_at)
        self.assertGreater(info.progress, 0.4)

    def test_signal_lost_over_ocean(self):
        self.sky = [self.plane(50.0, 1.5)]
        self.tr.step("BA11", self.started, self.t0)
        self.sky = []
        _, info, _ = self.tr.step("BA11", self.started, self.t0 + dt.timedelta(minutes=10))
        self.assertEqual(info.phase, "lost")

    def test_unknown_flight_still_returns_screen(self):
        data.lookup_flight = lambda f: None
        ac, info, ok = tracker.Tracker().step("ZZ9", self.started, self.t0)
        self.assertEqual((info.phase, ok), ("waiting", False))

    def test_status_line(self):
        from flightboard.render import track_line
        info = data.TrackInfo(flight="BA11", phase="cruising", progress=0.58, departed_at=self.t0,
                              remaining_nm=95, eta=self.t0 + dt.timedelta(minutes=48))
        ac = data.Aircraft(hex="x", origin="LHR", **{"destination_name": "Paris"})
        line = track_line(ac, info, {"speed": "mph"})
        self.assertEqual(line, "58% | Left LHR 18:00 | Cruising | 109 mi to go | ETA 18:48")
        lost = data.TrackInfo(flight="BA11", phase="lost", progress=0.5, departed_at=self.t0,
                              last_seen=self.t0 + dt.timedelta(minutes=30))
        self.assertEqual(track_line(ac, lost, {}, self.t0 + dt.timedelta(minutes=42)),
                         "Signal lost, last seen 12m ago | 50% | Left LHR 18:00")


class SavingToSD(unittest.TestCase):
    def test_spotted_count_is_not_written_for_every_aircraft(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            log = easter.SpotLog(p)
            log.record("a", 1000.0)                       # first sighting: saved
            for i, h in enumerate("bcdefg"):
                log.record(h, 1010.0 + i * 10)            # six more within five minutes: no more writes
            self.assertEqual(easter.read_count(p), 1)
            log.flush(now=1070.0)                          # clean shutdown writes the latest
            self.assertEqual(easter.read_count(p), 7)
            log.record("z", 1070.0 + 301)                  # more than five minutes after the last save (the flush at 1070)
            self.assertEqual(easter.read_count(p), 8)

    def test_milestones_are_saved_immediately(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            log = easter.SpotLog(p)
            log.record("a", 1000.0)
            log.count = 98
            log.record("b", 1010.0)                        # 99: not saved yet
            self.assertEqual(easter.read_count(p), 1)
            log.record("c", 1020.0)                        # 100: milestone, saved at once
            self.assertEqual(easter.read_count(p), 100)

    def test_config_survives_a_restart_and_a_half_written_temp_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            cfg = config.load(p); cfg.update(name="Sam", greeting="Hi Sam!", events=[{"date": "03-14", "text": "Happy Birthday", "kind": "birthday"}])
            config.save(cfg, p)
            (Path(d) / ".config-crashed.tmp").write_text('{"name": "half writ')   # what a power cut mid-save leaves behind
            again = config.load(p)                          # next boot reads the last complete file
            self.assertEqual((again["name"], again["greeting"], again["events"][0]["date"]), ("Sam", "Hi Sam!", "03-14"))


class ResetCount(unittest.TestCase):
    def test_reset_zeroes_count_and_lets_planes_count_again(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            log = easter.SpotLog(p)
            log.count = 49
            log.record("abc", 1000.0)                                 # 50: a milestone
            self.assertEqual(easter.read_count(p), 50)
            self.assertFalse(log.apply_reset(None))                  # no request: nothing happens
            self.assertTrue(log.apply_reset("t1", now=1100.0))
            self.assertEqual((log.count, easter.read_count(p)), (0, 0))
            self.assertEqual(log.record("abc", 1200.0), (1, False))  # same plane counts again, it was forgotten
            self.assertFalse(log.apply_reset("t1"))                  # same request again: ignored
            self.assertEqual(log.count, 1)

    def test_reset_is_not_repeated_after_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            log = easter.SpotLog(p)
            log.apply_reset("t1", now=1.0)
            log.record("a", 1000.0); log.record("b", 1001.0); log.flush(now=1002.0)
            again = easter.SpotLog(p)                                 # reboot: config.json still holds token t1
            self.assertFalse(again.apply_reset("t1"))                # must not wipe the count a second time
            self.assertEqual(again.count, 2)
            self.assertTrue(again.apply_reset("t2"))                 # a new request does
            self.assertEqual(again.count, 0)

    def test_milestones_restart_after_reset(self):
        with tempfile.TemporaryDirectory() as d:
            log = easter.SpotLog(Path(d) / "state.json")
            log.count = 120
            log.apply_reset("t1", now=1.0)
            hits = [log.record(f"p{i}", 1000.0 + i)[1] for i in range(50)]
            self.assertEqual(hits.index(True), 49)                   # the 50th plane after the reset


class Config(unittest.TestCase):
    def test_night_schedule_wraps_midnight(self):
        cfg = {"brightness": 80, "night": {"enabled": True, "start": "23:00", "end": "07:00", "brightness": 5}}
        at = lambda h, m: dt.datetime(2026, 1, 1, h, m)
        self.assertEqual(effective_brightness(cfg, at(23, 30)), 5)
        self.assertEqual(effective_brightness(cfg, at(2, 0)), 5)
        self.assertEqual(effective_brightness(cfg, at(7, 0)), 80)
        self.assertEqual(effective_brightness(cfg, at(12, 0)), 80)
        cfg["night"]["enabled"] = False
        self.assertEqual(effective_brightness(cfg, at(2, 0)), 80)

    def test_roundtrip_and_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            self.assertEqual(config.load(p)["radius_nm"], 15)  # missing file -> defaults
            cfg = config.load(p); cfg["radius_nm"] = 30; cfg["night"]["start"] = "22:00"
            config.save(cfg, p)
            got = config.load(p)
            self.assertEqual((got["radius_nm"], got["night"]["start"], got["night"]["end"]), (30, "22:00", "07:00"))
            p.write_text("{not json")
            self.assertEqual(config.load(p)["radius_nm"], 15)  # corrupt -> defaults, no crash


class Web(unittest.TestCase):
    def setUp(self):
        try:
            from flightboard import web
        except ImportError:
            self.skipTest("flask not installed")
        self.web, self.tmp = web, tempfile.TemporaryDirectory()
        web.CONFIG_PATH = Path(self.tmp.name) / "config.json"
        self.client = web.app.test_client()
        self._geo, web.data.geocode = web.data.geocode, lambda q: {"name": "Exeter, Devon, England", "lat": 50.72, "lon": -3.53} if q.upper().startswith(("EX", "EXETER")) else None

    def tearDown(self):
        self.web.data.geocode = self._geo
        self.tmp.cleanup()

    def test_page_and_save(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        r = self.client.post("/", data={"location": "Exeter", "radius_nm": "999", "brightness": "0", "speed": "kt",
                                         "altitude": "bogus", "night_enabled": "on", "night_start": "25:00", "night_end": "06:30",
                                         "night_brightness": "0"})
        self.assertEqual(r.status_code, 200)
        c = config.load(self.web.CONFIG_PATH)
        self.assertEqual((c["location"], c["place"], c["lat"], c["lon"]), ("Exeter", "Exeter, Devon, England", 50.72, -3.53))
        self.assertEqual((c["radius_nm"], c["brightness"]), (100, 1))  # clamped
        self.assertEqual((c["units"]["speed"], c["units"]["altitude"]), ("kt", "ft"))  # bad value ignored
        self.assertEqual((c["night"]["start"], c["night"]["end"], c["night"]["enabled"]), ("23:00", "06:30", True))

    def test_unknown_place_keeps_location(self):
        self.client.post("/", data={"location": "Exeter"})
        r = self.client.post("/", data={"location": "Nowhereville", "radius_nm": "20"})
        self.assertIn(b"find &#39;Nowhereville&#39;", r.data)
        c = config.load(self.web.CONFIG_PATH)
        self.assertEqual((c["location"], c["radius_nm"]), ("Exeter", 20))

    def test_personal_touches_save(self):
        self.client.post("/", data={"dwell_secs": "20", "name": "Sam", "greeting": "Hi Sam!", "events": "03-14 Happy Birthday Sam!",
                                     "rare_alerts": "on", "watch": "a388, rrr, pc12"})
        c = config.load(self.web.CONFIG_PATH)
        self.assertEqual((c["name"], c["greeting"]), ("Sam", "Hi Sam!"))
        self.assertEqual(c["dwell_secs"], 20)
        page = self.client.get("/").data.decode()
        self.assertIn('<option value="20" selected>', page)  # Medium is shown as chosen
        self.assertEqual(c["events"][0]["kind"], "birthday")
        self.assertEqual(c["easter"]["watch_types"], ["A388", "PC12"])
        self.assertEqual(c["easter"]["watch_callsigns"], ["RRR"])
        self.assertFalse(c["easter"]["milestones"])  # unticked box saves as off

    def test_track_start_stop(self):
        self.web.data.lookup_flight = lambda f: {"origin_name": "London Heathrow", "destination_name": "Paris"} if f == "BA11" else None
        r = self.client.post("/track", data={"flight": "ba 11"})
        self.assertIn(b"Tracking BA11", r.data)
        self.assertEqual(config.load(self.web.CONFIG_PATH)["track"]["flight"], "BA11")
        self.client.post("/", data={"radius_nm": "20"})  # saving other settings must not cancel tracking
        self.assertEqual(config.load(self.web.CONFIG_PATH)["track"]["flight"], "BA11")
        self.client.post("/track", data={"stop": "1"})
        self.assertEqual(config.load(self.web.CONFIG_PATH)["track"]["flight"], "")
        self.assertIn(b"like BA11", self.client.post("/track", data={"flight": "!!"}).data)
        self.assertIn(b"find a route", self.client.post("/track", data={"flight": "ZZ9"}).data)

    def test_reset_button_sets_a_request_and_shows_zero(self):
        page = self.client.get("/").data.decode()
        self.assertIn('formaction="/spotted/reset"', page)
        self.assertIn('id="resetform"', page)
        r = self.client.post("/spotted/reset")
        self.assertIn(b"Plane count reset to 0", r.data)
        self.assertIn(b"0 aircraft spotted so far", r.data)
        first = config.load(self.web.CONFIG_PATH)["spotted_reset"]
        self.assertTrue(first)
        import time as _t; _t.sleep(0.01)
        self.client.post("/spotted/reset")
        self.assertNotEqual(config.load(self.web.CONFIG_PATH)["spotted_reset"], first)  # each press is a new request

    def test_wifi_validation(self):
        r = self.client.post("/wifi", data={"ssid": "", "password": "x"})
        self.assertTrue(b"1-32" in r.data or b"NetworkManager" in r.data)  # rejected either way, nothing run


if __name__ == "__main__":
    unittest.main()
