import asyncio
import io
import os
import struct
import unittest
import zlib
from array import array
from pathlib import Path

from datetime import datetime, timedelta, timezone

from seestar_desk.album import delete_files, download_files, fits_radec, gallery_items, list_albums, list_folder
from seestar_desk.server import Desk
from seestar_desk.catalog import targets
from seestar_desk.discovery import discover
from seestar_desk.errors import ScopeError
from seestar_desk.fake_scope import FakeScope
from seestar_desk.framing import LineBuffer, dumps
from seestar_desk.imaging import JPEG, PREVIEW, RAW, FrameParser, build_frame, to_jpeg
from seestar_desk.plan import night_outlooks, summarize_night
from seestar_desk.sky import moon_sketch
from seestar_desk.session import ScopeSession, _absorb_device, _absorb_disk, _absorb_location
from seestar_desk.sky import alt_az, best_tonight, field_of_view, frame_fit, moon_advice, mosaic_centers, skip_reasons, sun_altitude


class ReadingTests(unittest.TestCase):
    def test_firmware_reports_storage_and_battery(self) -> None:
        state: dict = {}
        _absorb_disk(state, {"totalMB": 106330, "freeMB": 106071})
        self.assertEqual(state["disk_free"], 106071)
        self.assertEqual(state["disk_total"], 106330)
        _absorb_device(
            state,
            {
                "device": {"product_model": "Seestar S50 Pro"},
                "pi_status": {
                    "battery_capacity": 93,
                    "charge_online": False,
                    "charger_status": "Discharging",
                    "temp": 30.2,
                },
                "storage": {
                    "cur_storage": "emmc",
                    "storage_volume": [{"name": "emmc", "freeMB": 106071, "diskSizeMB": 119279}],
                },
            },
        )
        self.assertEqual(state["battery"], 93)
        self.assertIs(state["charging"], False)
        self.assertEqual(state["disk_free"], 106071)
        self.assertEqual(state["disk_total"], 119279)
        self.assertEqual(state["temperature"], 30.2)
        _absorb_device(
            state,
            {
                "balance_sensor": {"code": 0, "data": {"x": 0.01, "y": -0.02, "z": 1.0, "angle": 1.3}},
                "compass_sensor": {"code": 0, "data": {"direction": 184.2, "cali": 0}},
            },
        )
        self.assertEqual(state["level"]["angle"], 1.3)
        self.assertAlmostEqual(state["heading"], 94.2)
        located: dict = {}
        _absorb_location(located, [5.12, 52.09])
        self.assertEqual(located["longitude"], 5.12)
        self.assertEqual(located["latitude"], 52.09)
        _absorb_location(located, {"location_lon_lat": [4.5, 51.9]})
        self.assertEqual(located["longitude"], 4.5)
        self.assertEqual(located["latitude"], 51.9)


class FramingTests(unittest.TestCase):
    def test_splits_partial_lines(self) -> None:
        buffer = LineBuffer()
        first = dumps({"id": 1, "method": "test_connection"})
        second = dumps({"Event": "PiStatus", "battery_capacity": 80})
        self.assertEqual(buffer.feed(first[:8]), [])
        messages = buffer.feed(first[8:] + second)
        self.assertEqual(messages[0]["method"], "test_connection")
        self.assertEqual(messages[1]["Event"], "PiStatus")


class ImageTests(unittest.TestCase):
    def test_json_then_jpeg_frame(self) -> None:
        payload = b"\xff\xd8\xff\xd9"
        frame = build_frame(payload, JPEG, 0, 0)
        blob = b'{"id":1,"method":"test_connection","code":0,"result":"ok"}\r\n' + frame
        parser = FrameParser()
        items = parser.feed(blob[:6])
        items += parser.feed(blob[6:])
        self.assertEqual(items[0][0], "json")
        self.assertEqual(items[1][0], "frame")
        self.assertTrue(to_jpeg(items[1][1]).startswith(b"\xff\xd8"))

    def test_raw_and_zip_become_jpeg(self) -> None:
        pixels = array("H", [800, 4000, 1200] * 4)
        raw = pixels.tobytes()
        frame = build_frame(raw, RAW, 2, 2)
        parsed = FrameParser().feed(frame)[0][1]
        self.assertTrue(to_jpeg(parsed).startswith(b"\xff\xd8"))

        compressor = zlib.compressobj(wbits=-15)
        compressed = compressor.compress(raw) + compressor.flush()
        name = b"raw_data"
        header = struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            20,
            0,
            8,
            0,
            0,
            zlib.crc32(raw) & 0xFFFFFFFF,
            len(compressed),
            len(raw),
            len(name),
            0,
        ) + name
        zipped = FrameParser().feed(build_frame(header + compressed, RAW, 2, 2))[0][1]
        self.assertTrue(to_jpeg(zipped).startswith(b"\xff\xd8"))

    def test_preview_bayer(self) -> None:
        pixels = array("H", [100, 200, 300, 400] * 4)
        frame = build_frame(pixels.tobytes(), PREVIEW, 4, 4, data_type=2)
        parsed = FrameParser().feed(frame)[0][1]
        self.assertTrue(to_jpeg(parsed).startswith(b"\xff\xd8"))

    def test_preview_treats_the_pattern_as_grbg(self) -> None:
        from PIL import Image

        pixels = array("H", [1000, 8000, 100, 1000])
        frame = build_frame(pixels.tobytes(), PREVIEW, 2, 2, data_type=2)
        parsed = FrameParser().feed(frame)[0][1]
        image = Image.open(io.BytesIO(to_jpeg(parsed)))
        red, _green, blue = image.getpixel((0, 0))[:3]
        self.assertGreater(red, blue)

    def test_display_frame_keeps_the_long_edge(self) -> None:
        from seestar_desk.imaging import preview_and_display
        pixels = array("H", [100, 400, 800, 1200] * 16)
        frame = build_frame(pixels.tobytes(), PREVIEW, 8, 8, data_type=2)
        parsed = FrameParser().feed(frame)[0][1]
        preview, display = preview_and_display(parsed)
        self.assertTrue(preview.startswith(b"\xff\xd8"))
        self.assertTrue(display.startswith(b"\xff\xd8"))
        self.assertGreaterEqual(len(display), len(preview))


class CatalogTests(unittest.TestCase):
    def test_messier_list_is_complete(self) -> None:
        messier = [item for item in targets() if item["id"].startswith("M")]
        self.assertEqual(len(messier), 110)
        self.assertEqual(messier[0]["id"], "M1")
        orion = next(item for item in messier if item["id"] == "M42")
        self.assertAlmostEqual(orion["ra"], 5.583, places=2)
        self.assertLess(orion["dec"], 0)


class ScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fake = FakeScope()
        await self.fake.start()
        self.session = ScopeSession(self.fake.address())
        await self.session.connect()

    async def asyncTearDown(self) -> None:
        await self.session.disconnect()
        await self.fake.close()

    async def test_location_and_clock_are_read(self) -> None:
        self.assertEqual(self.session.state["latitude"], 52.09)
        self.assertEqual(self.session.state["longitude"], 5.12)
        self.assertEqual(self.session.state["clock"], "2026-11-07 21:04:00")
        self.assertEqual(self.session.state["temperature"], 31.4)
        self.assertEqual(self.session.state["ip"], "192.168.4.1")
        self.assertEqual(self.session.state["wifi_signal"], 80)
        self.assertEqual(self.session.state["firmware_label"], "9.75")
        self.assertEqual(self.session.state["disk_free"], 42000)

    async def test_events_and_motion_after_claim(self) -> None:
        self.assertEqual(self.session.state["model"], "S50 Pro")
        self.assertEqual(self.session.state["battery"], 86)
        with self.assertRaises(ScopeError):
            await self.session.move(90, 1000, 1)
        await self.session.claim()
        before = self.session.state["ra"]
        await self.session.move(90, 1000, 1)
        await self.session.command("scope_get_equ_coord")
        self.assertGreater(self.session.state["ra"], before)
        self.assertTrue(any(item["method"] == "scope_speed_move" for item in self.fake.received))

    async def test_album_download(self) -> None:
        folders = await list_albums(self.session)
        self.assertEqual(folders[0]["name"], "M 42")
        files = await list_folder(self.session, "M 42")
        self.assertEqual(files[0]["name"], "stacked.jpg")
        opened = [item for item in self.fake.received if item.get("method") == "get_img_file_page_number"]
        self.assertEqual(opened[0]["params"]["dir"], "MyWorks/M 42")
        dest = Path(self.id().replace(".", "_"))
        # Keep the download inside the workspace temp via the test directory.
        dest = Path.cwd() / ".test-downloads"
        saved = await asyncio.to_thread(
            download_files, self.session, "M 42", ["stacked_thn.jpg"], dest
        )
        self.assertEqual(Path(saved[0]).read_bytes(), self.fake.preview)
        Path(saved[0]).unlink()
        placed = await gallery_items(self.session)
        self.assertEqual(placed[0]["name"], "M 42")
        self.assertIsNotNone(placed[0]["ra"])
        self.assertGreater(placed[0]["dec"], -10)
        self.assertLess(placed[0]["dec"], 0)

    def test_fits_header_is_read_as_hours(self) -> None:
        def card(key: str, value: str) -> bytes:
            return f"{key:<8}= {value}".ljust(80).encode("ascii")

        header = b"".join([
            card("SIMPLE", "T"),
            card("CRVAL1", "83.8221"),
            card("CRVAL2", "-5.3912"),
            b"END".ljust(80),
        ])
        ra, dec = fits_radec(header)
        self.assertAlmostEqual(ra, 83.8221 / 15.0)
        self.assertAlmostEqual(dec, -5.3912)

    async def test_verify_flag_is_remembered(self) -> None:
        await self.session.disconnect()
        await self.fake.close()
        self.fake = FakeScope(require_verify=True)
        await self.fake.start()
        self.session = ScopeSession(self.fake.address())
        await self.session.connect()
        await self.session.command("get_device_state")
        self.assertTrue(self.session._verify)
        sent = [item for item in self.fake.received if item.get("method") == "get_device_state"]
        self.assertTrue(sent[-1]["params"]["verify"])

    async def test_rejected_verify_is_removed(self) -> None:
        await self.session.disconnect()
        await self.fake.close()
        self.fake = FakeScope(reject_verify=True)
        await self.fake.start()
        self.session = ScopeSession(self.fake.address())
        await self.session.connect()
        self.session._verify = True
        await self.session.command("get_setting")
        self.assertFalse(self.session._verify)

    async def test_unsupported_claim_still_grants_control(self) -> None:
        await self.session.disconnect()
        await self.fake.close()
        self.fake = FakeScope(master_unsupported=True)
        await self.fake.start()
        self.session = ScopeSession(self.fake.address())
        await self.session.connect()
        await self.session.claim()
        self.assertTrue(self.session.master)
        self.assertEqual(self.session.state["last_error"], "")
        await self.session.move(90, 1000, 1)
        self.assertTrue(any(item.get("method") == "scope_speed_move" for item in self.fake.received))

    async def test_restart_and_shutdown_commands(self) -> None:
        await self.session.claim()
        await self.session.power("restart")
        self.assertFalse(self.session.connected)
        self.assertTrue(any(item.get("method") == "pi_reboot" for item in self.fake.received))
        await self.session.connect()
        await self.session.claim()
        await self.session.power("shutdown")
        self.assertTrue(any(item.get("method") == "pi_shutdown" for item in self.fake.received))

    async def test_challenge_is_signed(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        dest = Path.cwd() / ".test-downloads"
        dest.mkdir(exist_ok=True)
        pem_path = dest / "test-key.pem"
        pem_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        previous = os.environ.get("SEESTAR_KEY_PATH")
        os.environ["SEESTAR_KEY_PATH"] = str(pem_path)
        await self.session.disconnect()
        await self.fake.close()
        self.fake = FakeScope(auth_public=key.public_key())
        await self.fake.start()
        self.session = ScopeSession(self.fake.address())
        try:
            await self.session.connect()
            signed = [item for item in self.fake.received if item.get("method") == "verify_client"]
            self.assertEqual(signed[-1]["params"]["data"], "desk-challenge")
            self.assertTrue(self.session.connected)
        finally:
            if previous is None:
                os.environ.pop("SEESTAR_KEY_PATH", None)
            else:
                os.environ["SEESTAR_KEY_PATH"] = previous
            pem_path.unlink(missing_ok=True)

    async def test_missing_key_stops_the_connection(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa

        previous = os.environ.pop("SEESTAR_KEY_PATH", None)
        await self.session.disconnect()
        await self.fake.close()
        self.fake = FakeScope(auth_public=rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key())
        await self.fake.start()
        self.session = ScopeSession(self.fake.address())
        from seestar_desk.auth import find_key

        if find_key() is not None:
            self.skipTest("a real interoperability key is already installed")
        try:
            with self.assertRaises(ScopeError) as caught:
                await self.session.connect()
            self.assertIn("seestar.pem", str(caught.exception))
            self.assertFalse(self.session.connected)
        finally:
            if previous is not None:
                os.environ["SEESTAR_KEY_PATH"] = previous


    async def test_live_view_and_stack_progress(self) -> None:
        await self.session.claim()
        await self.session.live("tele", True)
        for _ in range(30):
            if self.session.frames.get("tele"):
                break
            await asyncio.sleep(0.05)
        self.assertTrue(self.session.frames.get("tele", b"").startswith(b"\xff\xd8"))
        await self.session.stack(True, 80)
        self.assertEqual(self.session.state["stack"]["stacked"], 3)
        self.assertEqual(self.session.state["stack"]["dropped"], 1)
        self.assertEqual(self.session.state["stack"]["elapsed"], 12)
        for _ in range(30):
            if self.session.frames.get("stack"):
                break
            await asyncio.sleep(0.05)
        self.assertTrue(self.session.frames.get("stack", b"").startswith(b"\xff\xd8"))
        self.session.state["stack"]["state"] = "complete"
        await self.session.live("wide", True)
        for _ in range(30):
            if self.session.frames.get("wide"):
                break
            await asyncio.sleep(0.05)
        self.assertEqual(self.fake.cam_id, 1)
        self.assertTrue(self.session.frames.get("wide", b"").startswith(b"\xff\xd8"))

    async def test_a_new_target_clears_the_stacked_picture(self) -> None:
        await self.session.claim()
        self.session.state["target"] = "M 42"
        self.session.frames["stack"] = b"\xff\xd8old"
        self.session.frame_seq["stack"] = 4
        self.session.state["stack"] = {"state": "working", "stacked": 9}
        await self.session.goto(5.6, -5.4, "M 31", "star", False)
        self.assertEqual(self.session.state["target"], "M 31")
        self.assertNotIn("stack", self.session.frames)
        self.assertEqual(self.session.frame_seq["stack"], 0)
        self.assertEqual(self.session.state["stack"], {})
        self.session.frames["stack"] = b"\xff\xd8new"
        self.session.frame_seq["stack"] = 1
        await self.session.goto(0.7, 41.3, "M 31", "star", False)
        self.assertEqual(self.session.frames["stack"], b"\xff\xd8new")

    async def test_stack_is_not_stopped_to_open_the_preview(self) -> None:
        await self.session.claim()
        self.session.state["camera"] = "wide"
        await self.session.stack(True, None)
        methods = [item.get("method") for item in self.fake.received]
        started = methods.index("iscope_start_stack")
        self.assertNotIn("iscope_stop_view", methods[started:])
        self.assertNotIn("iscope_start_view", methods[started:])
        self.assertTrue(self.fake.stacking)
        self.assertEqual(self.session.state["stack"]["state"], "working")
        client = self.session._streams.get("tele")
        self.assertIsNotNone(client)
        self.assertFalse(client.preview)

    async def test_dark_library_is_left_running(self) -> None:
        await self.session.claim()
        self.session.state["view"] = {"stage": "DarkLibrary", "state": "working"}
        before = len(self.fake.received)
        await self.session.live("tele", True)
        methods = [item.get("method") for item in self.fake.received[before:]]
        self.assertNotIn("iscope_stop_view", methods)
        self.assertNotIn("iscope_start_view", methods)
        self.assertFalse(self.session._streams["tele"].preview)

    async def test_mode_switch_waits_until_the_mount_stops(self) -> None:
        await self.session.claim()
        self.fake.park_refusals = 1
        await self.session.set_mount_mode(False)
        self.assertFalse(self.session.state["equatorial"])
        self.assertTrue(self.session.state["parked"])
        self.assertEqual(self.session.state["last_error"], "")
        parks = [item for item in self.fake.received if item.get("method") == "scope_park"]
        self.assertGreaterEqual(len(parks), 2)
        self.assertEqual(parks[-1].get("params"), {"equ_mode": False})

    async def test_park_stops_the_image_stream(self) -> None:
        await self.session.claim()
        await self.session.live("tele", True)
        for _ in range(30):
            if self.session._streams.get("tele") and self.session._streams["tele"].running:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(self.session._streams.get("tele"))
        await self.session.park()
        self.assertTrue(self.session.state["parked"])
        self.assertEqual(self.session._streams, {})
        await self.session.live("tele", True)
        self.assertEqual(self.session._streams, {})

    async def test_video_wifi_delete_and_plan(self) -> None:
        await self.session.claim()
        await self.session.start_video("planet", 0)
        if self.session._video_task:
            await self.session._video_task
        methods = [item.get("method") for item in self.fake.received]
        self.assertIn("start_scan_planet", methods)
        self.assertIn("iscope_stop_view", methods)
        networks = await self.session.wifi_scan()
        self.assertEqual(networks[0]["ssid"], "Garden")
        await self.session.wifi_join("Garden", "secret")
        self.assertEqual(self.session.state["wifi"], "Garden")
        removed = await delete_files(self.session, "M 42", ["stacked.jpg"])
        self.assertEqual(removed, ["stacked.jpg"])
        self.assertEqual(self.fake.deleted[-1], "MyWorks/M 42/stacked.jpg")
        self.session.state["latitude"] = 51.5
        self.session.state["longitude"] = -0.1
        self.session.night.replace(
            {
                "items": [
                    {
                        "name": "M42",
                        "ra": 5.583,
                        "dec": -5.383,
                        "mode": "star",
                        "minutes": 0,
                        "gain": 80,
                        "filter": 1,
                        "autofocus": True,
                        "rows": 1,
                        "cols": 1,
                    }
                ],
                "safety": {"min_battery": 0, "min_altitude": -90, "stop_before_dawn": False, "max_cloud": 100},
                "options": {"park_when_finished": False},
            }
        )
        self.session.night.start()
        assert self.session.night._task is not None
        await self.session.night._task
        self.assertEqual(self.session.night.status["step"], "complete")
        self.assertTrue(any(item.get("method") == "iscope_start_stack" for item in self.fake.received))

    async def test_polar_offset_is_kept(self) -> None:
        await self.session.claim()
        await self.session.polar_align(True, 3)
        self.assertEqual(self.session.state["align"]["x"], 1.2)
        self.assertEqual(self.session.state["align"]["y"], -0.4)


class SkyTests(unittest.TestCase):
    def test_summer_noon_and_mosaic_grid(self) -> None:
        noon = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
        midnight = datetime(2026, 12, 21, 0, tzinfo=timezone.utc)
        self.assertGreater(sun_altitude(51.5, 0, noon), 40)
        self.assertLess(sun_altitude(51.5, 0, midnight), -18)
        lst_ra = 5.0
        high, _az = alt_az(lst_ra, 51.5, 51.5, 0, noon)
        self.assertGreater(high, -90)
        centers = mosaic_centers(5.5, 10, 2, 2, 20, *field_of_view("S50 Pro"))
        self.assertEqual(len(centers), 4)
        self.assertEqual(len({(round(item["ra"], 4), round(item["dec"], 4)) for item in centers}), 4)
        reasons = skip_reasons(altitude=10, battery=15, sun_alt=-20, cloud=90, safety={"min_altitude": 25, "min_battery": 20, "stop_before_dawn": True, "max_cloud": 80})
        self.assertEqual(len(reasons), 3)

    def test_best_pick_is_high_during_the_dark_hours(self) -> None:
        evening = datetime(2026, 11, 7, 18, tzinfo=timezone.utc)
        report = best_tonight(52.34, 6.11, evening)
        self.assertEqual(report["window"]["darkness"], "astronomical night")
        self.assertGreaterEqual(report["picks"][0]["peak_altitude"], 50)
        self.assertGreater(report["picks"][0]["dec"], 0)
        self.assertEqual(report["picks"], sorted(report["picks"], key=lambda item: item["score"], reverse=True))
        noon = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
        summer = best_tonight(52.34, 6.11, noon)
        self.assertGreater(summer["window"]["start"], noon.isoformat())

    def test_frame_fit_and_dual_band_moon(self) -> None:
        galaxy = frame_fit(178, "Seestar S50 Pro")
        self.assertIn("mosaic", galaxy["fit"].lower())
        self.assertGreaterEqual(galaxy["fit_rows"] * galaxy["fit_cols"], 2)
        self.assertEqual(frame_fit(1.4, "S50")["fit"], "Tiny in the frame")
        self.assertEqual(frame_fit(85, "S50")["fit"], "A tight crop")
        close = moon_advice("nebula", 20, True, False)
        self.assertIn("20", close)
        self.assertNotIn("dual-band", close)
        allowed = moon_advice("nebula", 20, True, True)
        self.assertIn("dual-band", allowed)
        blocked = moon_advice("nebula", 8, True, True)
        self.assertNotIn("dual-band", blocked)
        self.assertIn("8", blocked)

    def test_tonight_picks_the_clearest_dark_hours(self) -> None:
        start = datetime(2026, 11, 8, 18, tzinfo=timezone.utc)
        clouds = [80, 70, 20, 10, 10, 15, 60, 90]
        samples = []
        for index, cloud in enumerate(clouds):
            moment = start + timedelta(hours=index)
            samples.append({
                "time": moment,
                "label": f"{moment.hour:02d}:00",
                "end": f"{(moment.hour + 1) % 24:02d}:00",
                "cloud": cloud,
                "temp": 4.0,
                "dew": 1.0,
                "humidity": 80,
                "wind": 3,
                "gust": 5,
            })
        report = summarize_night(samples, 51.5, -0.1, start)
        self.assertGreaterEqual(len(report["hours"]), 4)
        self.assertIn("–", report["window"])
        self.assertEqual(report["dew_risk"], "Moderate")
        self.assertLessEqual(report["low"], 4)

    def test_outlook_scores_a_dark_stretch(self) -> None:
        sketch = moon_sketch(datetime(2026, 11, 12, 22, tzinfo=timezone.utc))
        self.assertGreaterEqual(sketch["illumination"], 0)
        self.assertLessEqual(sketch["illumination"], 1)
        self.assertTrue(sketch["name"])
        start = datetime(2026, 11, 12, 16, tzinfo=timezone.utc)
        samples = []
        for index in range(40):
            moment = start + timedelta(hours=index)
            samples.append({
                "time": moment,
                "label": f"{moment.hour:02d}:00",
                "end": f"{(moment.hour + 1) % 24:02d}:00",
                "cloud": 20 if index < 20 else 80,
                "temp": 3.0,
                "dew": 0.0,
                "humidity": 70,
                "wind": 3,
                "gust": 4,
                "precip": 0,
                "day": "Thursday",
                "date": "Nov 12",
            })
        nights = night_outlooks(samples, 52.3, 6.1)
        self.assertGreaterEqual(len(nights), 1)
        self.assertIn(nights[0]["label"], {"Excellent", "Great", "Good", "Fair", "Poor", "Bad"})
        self.assertGreaterEqual(nights[0]["score"], 0)
        self.assertLessEqual(nights[0]["score"], 100)
        self.assertTrue(nights[0]["spark"])


class DeskMemoryTests(unittest.TestCase):
    def test_remembered_scopes_keep_a_badge_and_place(self) -> None:
        desk = Desk()
        folder = Path(__file__).parent / ".tmp-desk"
        folder.mkdir(exist_ok=True)
        desk.path = folder / "scopes.json"
        desk.known.clear()
        desk.place = None
        desk.path.write_text(
            '{"scopes":[{"host":"10.0.0.8","name":"Garden","model":"Seestar S50 Pro","ssid":"Home"}]}',
            encoding="utf-8",
        )
        desk._load()
        described = desk.describe(desk.known["10.0.0.8"])
        self.assertTrue(described["linked"])
        self.assertEqual(described["model"], "Seestar S50 Pro")
        self.assertEqual(desk.remember_place(51.5, -0.12), {"latitude": 51.5, "longitude": -0.12})
        again = Desk()
        again.path = desk.path
        again.known.clear()
        again.place = None
        again._load()
        self.assertEqual(again.place["latitude"], 51.5)
        self.assertTrue(again.describe(again.known["10.0.0.8"])["linked"])
        added = again.add_host("10.0.0.9", "New")
        self.assertFalse(again.describe(added)["linked"])
        folder.joinpath("scopes.json").unlink(missing_ok=True)


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_udp_beacon(self) -> None:
        fake = FakeScope()
        await fake.start_udp()
        try:
            found = await discover(timeout=0.4, targets=[("127.0.0.1", fake.udp_port)])
        finally:
            await fake.close()
        self.assertEqual(found[0].host, "127.0.0.1")
        self.assertEqual(found[0].model, "S50 Pro")


if __name__ == "__main__":
    unittest.main()
