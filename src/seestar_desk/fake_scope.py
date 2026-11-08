"""An in-process Seestar used by tests and the demo mode."""

from __future__ import annotations

import asyncio
import io
import json
import urllib.parse
from typing import Any

from PIL import Image, ImageDraw

from seestar_desk.discovery import FoundScope
from seestar_desk.framing import LineBuffer, dumps
from seestar_desk.imaging import JPEG, PREVIEW, build_frame
from seestar_desk.session import GUEST_OK

READ_ONLY = GUEST_OK | {"set_user_location", "pi_set_time", "pi_output_set2"}


def _camera_id(value: Any) -> int:
    if value in {"SecondView", "wide", 1, "1"}:
        return 1
    if value in {"View", "tele", 0, "0"}:
        return 0
    return int(value)


class FakeScope:
    def __init__(
        self,
        require_verify: bool = False,
        reject_verify: bool = False,
        auth_public: Any = None,
        master_unsupported: bool = False,
    ) -> None:
        self.require_verify = require_verify
        self.reject_verify = reject_verify
        self.auth_public = auth_public
        self.master_unsupported = master_unsupported
        self.challenge = "desk-challenge"
        self.received: list[dict[str, Any]] = []
        self.master = False
        self.ra = 5.583
        self.dec = -5.383
        self.alt = 41.2
        self.az = 168.0
        self.focus = 1580
        self.filter = 1
        self.tracking = True
        self.equatorial = False
        self.parked = False
        self.stacking = False
        self.cam_id = 0
        self.settings: dict[str, Any] = {
            "exp_ms": {"stack_l": 10000, "continuous": 10000},
            "stack_dither": {"pix": 50, "interval": 5, "enable": False},
            "heater_enable": False,
        }
        self.files: dict[str, bytes] = {}
        self.preview = b""
        self._servers: list[asyncio.Server] = []
        self._udp = None
        self.control_port = 0
        self.image_port = 0
        self.wide_port = 0
        self.http_port = 0
        self.udp_port = 0

    async def start(self) -> None:
        self.preview = _demo_jpeg()
        self.files["/MyWorks/M 42/stacked_thn.jpg"] = self.preview
        self.files["/MyWorks/M 42/stacked.jpg"] = self.preview
        self._servers = [
            await asyncio.start_server(self._control, "127.0.0.1", 0),
            await asyncio.start_server(self._image, "127.0.0.1", 0),
            await asyncio.start_server(self._image, "127.0.0.1", 0),
            await asyncio.start_server(self._http, "127.0.0.1", 0),
        ]
        self.control_port = _port(self._servers[0])
        self.image_port = _port(self._servers[1])
        self.wide_port = _port(self._servers[2])
        self.http_port = _port(self._servers[3])

    async def start_udp(self) -> None:
        loop = asyncio.get_running_loop()

        class Protocol(asyncio.DatagramProtocol):
            def __init__(self, scope: FakeScope) -> None:
                self.scope = scope
                self.transport = None

            def connection_made(self, transport) -> None:  # type: ignore[no-untyped-def]
                self.transport = transport

            def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[no-untyped-def]
                payload = dumps(
                    {
                        "id": 1,
                        "result": {
                            "ip": "127.0.0.1",
                            "name": "Desk demo",
                            "model": "S50 Pro",
                            "ssid": "Demo-WiFi",
                        },
                    }
                )
                self.transport.sendto(payload, addr)

        transport, _protocol = await loop.create_datagram_endpoint(
            lambda: Protocol(self),
            local_addr=("127.0.0.1", 0),
        )
        self._udp = transport
        self.udp_port = transport.get_extra_info("sockname")[1]

    def address(self) -> FoundScope:
        return FoundScope(
            host="127.0.0.1",
            name="Desk demo",
            model="S50 Pro",
            ssid="Demo-WiFi",
            control_port=self.control_port,
            image_port=self.image_port,
            wide_port=self.wide_port,
            http_port=self.http_port,
            raw={"demo": True},
        )

    async def close(self) -> None:
        if self._udp is not None:
            self._udp.close()
            self._udp = None
        servers = self._servers
        self._servers = []
        for server in servers:
            server.close()
            await server.wait_closed()

    async def _control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        lines = LineBuffer()
        lock = asyncio.Lock()

        async def send(message: dict[str, Any]) -> None:
            async with lock:
                writer.write(dumps(message))
                await writer.drain()

        await send(
            {
                "Event": "PiStatus",
                "battery_capacity": 86,
                "charge_online": False,
                "temp": 18.5,
            }
        )
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                for message in lines.feed(data):
                    self.received.append(message)
                    reply = self._answer(message)
                    if reply is not None:
                        await send(reply)
                        follow = self._followup(message)
                        if follow is not None:
                            await send(follow)
        finally:
            writer.close()

    def _answer(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = str(message.get("method") or "")
        params = message.get("params")
        command_id = message.get("id")
        if method == "test_connection":
            return _ok(command_id, method, "ok")
        if method == "get_verify_str":
            if self.auth_public is None:
                return _fail(command_id, method, 103, "method not found")
            return _ok(command_id, method, {"str": self.challenge})
        if method == "verify_client":
            return self._check_signature(command_id, method, params)
        if self.reject_verify and isinstance(params, dict) and params.get("verify") is True:
            return _fail(command_id, method, 107, "unexpected verify")
        if self.require_verify and not (isinstance(params, dict) and params.get("verify") is True):
            return _fail(command_id, method, 113, "verify required")
        if (
            method == "set_setting"
            and self.master_unsupported
            and isinstance(params, dict)
            and "master_cli" in params
        ):
            return _fail(command_id, method, 210, "not supported")
        if not (self.master or self.master_unsupported) and method not in READ_ONLY:
            return _fail(command_id, method, 403, "guest mode: another app has control")
        result = self._result(method, params)
        return _ok(command_id, method, result)

    def _check_signature(self, command_id: Any, method: str, params: Any) -> dict[str, Any]:
        if self.auth_public is None or not isinstance(params, dict):
            return _fail(command_id, method, 103, "method not found")
        import base64

        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        try:
            self.auth_public.verify(
                base64.b64decode(params.get("sign") or ""),
                str(params.get("data") or "").encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA1(),
            )
        except (InvalidSignature, ValueError):
            return _fail(command_id, method, 1, "bad signature")
        return _ok(command_id, method, {"ok": True})

    def _followup(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "iscope_start_stack" and self.master:
            return {
                "Event": "Stack",
                "state": "working",
                "stacked_frame": 3,
                "dropped_frame": 1,
                "elapsed": 12,
            }
        if method == "start_auto_focuse" and self.master:
            return {"Event": "AutoFocus", "state": "complete", "position": self.focus}
        if method == "start_polar_align" and self.master:
            return {"Event": "3PPA", "state": "working", "state_code": 1, "x": 1.2, "y": -0.4}
        if method == "iscope_start_view" and self.master:
            return {"Event": "AutoGoto", "state": "complete", "target_name": (message.get("params") or {}).get("target_name")}
        return None

    def _result(self, method: str, params: Any) -> Any:
        if method == "set_setting" and isinstance(params, dict):
            if "master_cli" in params:
                self.master = bool(params["master_cli"])
            if "selected_cam" in params:
                self.cam_id = _camera_id(params["selected_cam"])
            if "wide_cam" in params:
                self.cam_id = 1 if params["wide_cam"] else 0
            stored = {key: value for key, value in params.items() if key != "verify"}
            self.settings.update(stored)
            return {"master_cli": self.master}
        if method == "get_device_state":
            return {
                "device": {
                    "name": "Desk demo",
                    "model": "Seestar S50 Pro",
                    "firmware_ver_int": 2975,
                    "battery_capacity": 86,
                    "charge_online": False,
                    "temp": 31.4,
                },
                "mount": {"equ_mode": self.equatorial, "close": self.parked, "track": self.tracking},
                "storage": {"free_mb": 42000, "total_mb": 58000},
                "location_lon_lat": [6.1114, 52.3372],
                "balance_sensor": {"code": 0, "data": {"x": 0.02, "y": -0.012, "z": 0.999, "angle": 1.32}},
                "compass_sensor": {"code": 0, "data": {"direction": 12.4}},
                "setting": {"wide_cam": self.cam_id == 1},
            }
        if method in {"scope_get_equ_coord", "scope_get_ra_dec"}:
            return {"ra": self.ra, "dec": self.dec}
        if method == "scope_get_horiz_coord":
            return {"alt": self.alt, "az": self.az}
        if method == "get_disk_volume":
            return {"free_mb": 42000, "total_mb": 58000}
        if method == "get_view_state":
            return {
                "mode": "star",
                "stage": "Stack" if self.stacking else "ContinuousExposure",
                "target_name": "M 42",
                "cam_id": self.cam_id,
            }
        if method == "get_focuser_position":
            return self.focus
        if method == "get_wheel_position":
            return {"position": self.filter}
        if method == "get_setting":
            return self.settings
        if method == "pi_station_state":
            return {
                "ssid": getattr(self, "station_ssid", "Demo-WiFi"),
                "sig_lev": 80,
                "ip": "192.168.4.1",
            }
        if method == "pi_station_scan":
            return [{"ssid": "Garden", "rssi": -55}, {"ssid": "Demo-WiFi", "rssi": -40}]
        if method == "pi_station_set" and isinstance(params, dict):
            self.station_ssid = str(params.get("ssid") or "")
            return "ok"
        if method == "pi_station_open":
            return "ok"
        if method == "start_scan_planet":
            return "ok"
        if method == "delete_image":
            self.deleted = getattr(self, "deleted", [])
            self.deleted.append(params)
            return "ok"
        if method == "pi_is_verified":
            return True
        if method == "get_user_location":
            return [getattr(self, "longitude", 5.12), getattr(self, "latitude", 52.09)]
        if method == "set_user_location" and isinstance(params, dict):
            self.latitude = params.get("lat")
            self.longitude = params.get("lon")
            return {"lat": self.latitude, "lon": self.longitude}
        if method == "pi_get_time":
            return {"year": 2026, "mon": 11, "day": 7, "hour": 21, "min": 4, "sec": 0}
        if method == "scope_speed_move" and isinstance(params, dict):
            self.ra = round((self.ra + 0.02) % 24, 4)
            self.alt = round(self.alt + 0.1, 2)
            return "ok"
        if method == "scope_set_track_state" and isinstance(params, dict):
            self.tracking = bool(params.get("track"))
            return {"track": self.tracking}
        if method == "move_focuser" and isinstance(params, dict):
            self.focus = int(params.get("step") or self.focus)
            return self.focus
        if method == "set_wheel_position" and isinstance(params, dict):
            self.filter = int(params.get("position") or 0)
            return {"position": self.filter}
        if method == "iscope_start_stack":
            self.stacking = True
            return "ok"
        if method == "iscope_stop_view":
            self.stacking = False
            return "ok"
        if method == "iscope_start_view" and isinstance(params, dict) and (
            "cam_id" in params or "selected_cam" in params
        ):
            self.cam_id = _camera_id(params.get("selected_cam", params.get("cam_id")))
            return "ok"
        if method == "scope_park":
            self.parked = True
            if isinstance(params, dict) and "equ_mode" in params:
                self.equatorial = bool(params["equ_mode"])
            return "ok"
        if method == "scope_move_to_horizon":
            self.parked = False
            self.alt = 0
            return "ok"
        if method == "get_albums":
            return {
                "path": "MyWorks",
                "list": [
                    {
                        "group_name": "DeepSky",
                        "files": [
                            {
                                "name": "M 42",
                                "thn": "M 42/stacked_thn.jpg",
                                "count": 1,
                                "type": 0,
                            }
                        ],
                    }
                ],
            }
        if method == "get_img_file_page_number":
            return 1
        if method == "get_img_file_page_name":
            return [
                {"name": "stacked.jpg", "size_k": 12, "date": "2026-11-07", "is_dir": False},
                {"name": "stacked_thn.jpg", "size_k": 4, "date": "2026-11-07", "is_dir": False},
            ]
        return "ok"

    async def _image(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        buffer = bytearray()
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                buffer.extend(data)
                while True:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        break
                    raw = bytes(buffer[:newline]).strip()
                    del buffer[: newline + 1]
                    if not raw:
                        continue
                    message = json.loads(raw)
                    method = message.get("method")
                    if method == "get_verify_str":
                        writer.write(dumps({"jsonrpc": "2.0", "id": message.get("id"), "method": method, "code": 103, "error": "method not found"}))
                        await writer.drain()
                        continue
                    if method in {"begin_streaming", "get_current_img"}:
                        writer.write(build_frame(self.preview, PREVIEW, 640, 360))
                    elif method == "get_stacked_img":
                        writer.write(build_frame(self.preview, JPEG, 0, 0))
                    writer.write(dumps({"jsonrpc": "2.0", "id": message.get("id"), "method": method, "code": 0, "result": "ok"}))
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            return
        finally:
            writer.close()

    async def _http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        except Exception:
            writer.close()
            return
        line = header.split(b"\r\n", 1)[0].decode("utf-8", errors="replace")
        parts = line.split(" ")
        path = urllib.parse.unquote(parts[1].split("?", 1)[0]) if len(parts) > 1 else "/"
        body = self.files.get(path)
        if body is None:
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        else:
            head = (
                "HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            ).encode("ascii")
            writer.write(head + body)
        await writer.drain()
        writer.close()


def _port(server: asyncio.Server) -> int:
    sockets = server.sockets or []
    return int(sockets[0].getsockname()[1])


def _ok(command_id: Any, method: str, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": command_id, "method": method, "code": 0, "result": result}


def _fail(command_id: Any, method: str, code: int, error: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": command_id, "method": method, "code": code, "error": error, "result": None}


def _demo_jpeg() -> bytes:
    image = Image.new("RGB", (640, 360), (8, 12, 22))
    draw = ImageDraw.Draw(image)
    for point in ((120, 80), (200, 140), (310, 90), (420, 160), (510, 70), (180, 240), (360, 220)):
        draw.ellipse((point[0] - 1, point[1] - 1, point[0] + 1, point[1] + 1), fill=(236, 228, 210))
    draw.ellipse((300, 150, 360, 210), fill=(92, 48, 62))
    draw.ellipse((318, 168, 344, 194), fill=(214, 176, 150))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()
