"""One control connection to a Seestar, plus its live image sockets."""

from __future__ import annotations

import asyncio
import socket
import time
import urllib.parse
from typing import Any, Awaitable, Callable

from seestar_desk.auth import MISSING_KEY, find_key, sign_challenge
from seestar_desk.discovery import FoundScope
from seestar_desk.errors import ScopeError
from seestar_desk.framing import LineBuffer, dumps
from seestar_desk.imaging import FrameParser, to_jpeg

UpdateHandler = Callable[[str, dict[str, Any]], Awaitable[None]]

GUEST_OK = {
    "test_connection",
    "get_device_state",
    "get_disk_volume",
    "get_view_state",
    "get_setting",
    "get_stack_setting",
    "get_stack_info",
    "get_camera_state",
    "get_camera_info",
    "get_focuser_position",
    "get_wheel_position",
    "get_wheel_setting",
    "get_wheel_state",
    "get_user_location",
    "get_albums",
    "get_img_file_page_number",
    "get_img_file_page_name",
    "pi_get_time",
    "pi_is_verified",
    "pi_station_state",
    "scope_get_equ_coord",
    "scope_get_ra_dec",
    "scope_get_horiz_coord",
    "set_setting",
}


class ScopeSession:
    def __init__(self, found: FoundScope) -> None:
        self.found = found
        self.host = found.host
        self.control_port = found.control_port
        self.image_port = found.image_port
        self.wide_port = found.wide_port
        self.http_port = found.http_port
        self.connected = False
        self.master: bool | None = None
        self._verify = False
        self._next_id = 1000
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._write_lock = asyncio.Lock()
        self.album_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self.on_update: UpdateHandler | None = None
        self.lines = LineBuffer()
        self.state: dict[str, Any] = {
            "id": found.id,
            "host": found.host,
            "name": found.name or found.host,
            "model": found.model,
            "ssid": found.ssid,
            "connected": False,
            "master": None,
            "battery": None,
            "charging": None,
            "disk_free": None,
            "disk_total": None,
            "ra": None,
            "dec": None,
            "alt": None,
            "az": None,
            "tracking": None,
            "equatorial": None,
            "parked": None,
            "focus": None,
            "filter": None,
            "view": {},
            "stack": {},
            "align": {},
            "wifi": found.ssid,
            "firmware": None,
            "temperature": None,
            "settings": {},
            "last_error": "",
            "mode": "",
            "target": "",
        }
        self.log: list[dict[str, Any]] = []
        self.frames: dict[str, bytes] = {}
        self.frame_seq: dict[str, int] = {"tele": 0, "wide": 0}
        self._streams: dict[str, ImageClient] = {}

    @property
    def id(self) -> str:
        return self.found.id

    def snapshot(self) -> dict[str, Any]:
        snap = dict(self.state)
        snap["connected"] = self.connected
        snap["master"] = self.master
        snap["frame_seq"] = dict(self.frame_seq)
        return snap

    async def connect(self) -> dict[str, Any]:
        if self.connected:
            return self.snapshot()
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.control_port),
            timeout=5,
        )
        self.connected = True
        self._tasks.append(asyncio.create_task(self._read_loop(), name=f"read-{self.id}"))
        try:
            await self._authenticate()
        except ScopeError:
            await self.disconnect()
            raise
        try:
            await self.command("test_connection", timeout=4)
            await self.command(
                "set_setting",
                {"cli_name": socket.gethostname() or "seestar-desk"},
                timeout=4,
            )
        except ScopeError as exc:
            if not _unsupported(exc):
                self.state["last_error"] = str(exc)
        self._tasks.append(asyncio.create_task(self._heartbeat(), name=f"beat-{self.id}"))
        self._tasks.append(asyncio.create_task(self._poll(), name=f"poll-{self.id}"))
        await self.refresh()
        await self._emit("state", self.snapshot())
        return self.snapshot()

    async def disconnect(self) -> None:
        for stream in list(self._streams.values()):
            await stream.stop()
        self._streams.clear()
        self.connected = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        writer = self._writer
        self._writer = None
        self._reader = None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ScopeError("The telescope connection closed."))
        self._pending.clear()
        await self._emit("state", self.snapshot())

    async def _authenticate(self) -> None:
        """Sign the firmware challenge before any other command is accepted."""
        try:
            response = await self.command("get_verify_str", "verify", timeout=6)
        except ScopeError as exc:
            code = (exc.response or {}).get("code")
            if code == 103:
                return
            raise
        result = response.get("result")
        challenge = result.get("str") if isinstance(result, dict) else result
        if not isinstance(challenge, str) or not challenge:
            return
        key = find_key()
        if key is None:
            raise ScopeError(MISSING_KEY)
        try:
            signature = sign_challenge(key, challenge)
        except Exception as exc:
            raise ScopeError(f"The key at {key} could not sign the telescope challenge: {exc}") from exc
        await self.command("verify_client", {"sign": signature, "data": challenge}, timeout=8)
        try:
            await self.command("pi_is_verified", "verify", timeout=6)
        except ScopeError:
            pass
        await self._note("Signed in with the current app handshake.")

    async def command(self, method: str, params: Any = None, timeout: float = 8) -> dict[str, Any]:
        async with self._write_lock:
            return await self._roundtrip(method, params, timeout)

    async def claim(self) -> dict[str, Any]:
        try:
            response = await self.command("set_setting", {"master_cli": True})
            await self._note("Control claimed on this computer.")
        except ScopeError as exc:
            if not _unsupported(exc):
                raise
            response = exc.response or {}
            await self._note("This firmware has no control lock. This computer can operate the telescope directly.")
        self.master = True
        self.state["master"] = True
        self.state["last_error"] = ""
        await self._emit("state", self.snapshot())
        return response

    async def release(self) -> dict[str, Any]:
        try:
            response = await self.command("set_setting", {"master_cli": False})
        except ScopeError as exc:
            if not _unsupported(exc):
                raise
            response = exc.response or {}
        self.master = False
        self.state["master"] = False
        await self._note("Control released.")
        await self._emit("state", self.snapshot())
        return response

    async def refresh(self) -> None:
        for method in (
            "get_device_state",
            "scope_get_equ_coord",
            "scope_get_horiz_coord",
            "get_disk_volume",
            "get_view_state",
            "get_focuser_position",
            "pi_station_state",
        ):
            try:
                await self.command(method, timeout=4)
            except ScopeError as exc:
                self.state["last_error"] = str(exc)
                break

    async def goto(self, ra: float | None, dec: float | None, name: str, mode: str, lp_filter: bool) -> dict[str, Any]:
        params: dict[str, Any] = {"mode": mode or "star", "target_name": name or "Target", "lp_filter": lp_filter}
        if ra is not None and dec is not None and mode in {"star", ""}:
            params["target_ra_dec"] = [ra, dec]
        response = await self.command("iscope_start_view", params)
        self.state["mode"] = params["mode"]
        self.state["target"] = params["target_name"]
        return response

    async def move(self, angle: float, speed: int, dur_sec: float) -> dict[str, Any]:
        return await self.command(
            "scope_speed_move",
            {"angle": int(angle) % 360, "speed": int(speed), "dur_sec": int(dur_sec)},
        )

    async def stop(self, stage: str | None = None) -> dict[str, Any]:
        params = {"stage": stage} if stage else None
        return await self.command("iscope_stop_view", params)

    async def stack(self, restart: bool, gain: int | None) -> dict[str, Any]:
        response = await self.command("iscope_start_stack", {"restart": restart})
        if gain is not None:
            try:
                await self.command("set_control_value", ["gain", int(gain)])
            except ScopeError as exc:
                self.state["last_error"] = str(exc)
        return response

    async def focus_move(self, position: int) -> dict[str, Any]:
        return await self.command("move_focuser", {"step": int(position), "ret_step": True})

    async def focus_auto(self) -> dict[str, Any]:
        return await self.command("start_auto_focuse")

    async def focus_stop(self) -> dict[str, Any]:
        return await self.command("stop_auto_focuse")

    async def filter_position(self, position: int) -> dict[str, Any]:
        return await self.command("set_wheel_position", {"position": int(position)})

    async def apply_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        last: dict[str, Any] = {}
        if "lat" in payload and "lon" in payload:
            last = await self.command(
                "set_user_location",
                {"lat": float(payload["lat"]), "lon": float(payload["lon"]), "force": True},
            )
        if payload.get("set_clock"):
            last = await self._set_clock()
        setting: dict[str, Any] = {}
        if "exp_ms" in payload:
            setting["exp_ms"] = payload["exp_ms"]
        if "stack_dither" in payload:
            setting["stack_dither"] = payload["stack_dither"]
        if "heater_enable" in payload:
            setting["heater_enable"] = bool(payload["heater_enable"])
        if setting:
            last = await self.command("set_setting", setting)
        if "dew_heater_power" in payload or "is_dew_on" in payload:
            last = await self.command(
                "pi_output_set2",
                {
                    "is_dew_on": bool(payload.get("is_dew_on", payload.get("heater_enable", False))),
                    "dew_heater_power": int(payload.get("dew_heater_power") or 0),
                },
            )
        await self.refresh()
        return last

    async def set_mount_mode(self, equatorial: bool) -> dict[str, Any]:
        await self.command("scope_move_to_horizon")
        return await self.command("scope_park", {"equ_mode": bool(equatorial)})

    async def power(self, action: str) -> dict[str, Any]:
        method = {"restart": "pi_reboot", "shutdown": "pi_shutdown"}.get(action)
        if method is None:
            raise ScopeError("Choose restart or shut down.")
        note = (
            "Restart sent. The telescope will drop off and come back on its own."
            if action == "restart"
            else "Shutdown sent. The telescope stays off until you switch it on."
        )
        try:
            await self.command(method, timeout=5)
        except ScopeError as exc:
            text = str(exc).lower()
            if not any(word in text for word in ("timed out", "closed", "connection", "lost")):
                raise
        await self._note(note)
        await self.disconnect()
        return self.snapshot()

    async def polar_align(self, restart: bool, dec_pos_index: int) -> dict[str, Any]:
        params: dict[str, Any] = {"restart": restart}
        if restart:
            params["dec_pos_index"] = int(dec_pos_index)
        return await self.command("start_polar_align", params, timeout=12)

    async def live(self, camera: str, enabled: bool) -> None:
        camera = "wide" if camera == "wide" else "tele"
        current = self._streams.get(camera)
        if not enabled:
            if current:
                await current.stop()
                self._streams.pop(camera, None)
            return
        if current and current.running:
            return
        port = self.wide_port if camera == "wide" else self.image_port
        client = ImageClient(self.host, port, camera, self._on_frame)
        self._streams[camera] = client
        await client.start()

    def file_url(self, path: str) -> str:
        quoted = urllib.parse.quote(path, safe="/")
        return f"http://{self.host}:{self.http_port}/{quoted}"

    async def _set_clock(self) -> dict[str, Any]:
        now = time.localtime()
        zone = time.tzname[1] if time.daylight and now.tm_isdst else time.tzname[0]
        return await self.command(
            "pi_set_time",
            {
                "year": now.tm_year,
                "mon": now.tm_mon,
                "day": now.tm_mday,
                "hour": now.tm_hour,
                "min": now.tm_min,
                "sec": now.tm_sec,
                "time_zone": zone or "UTC",
            },
        )

    async def _roundtrip(self, method: str, params: Any, timeout: float) -> dict[str, Any]:
        self._ensure()
        response = await self._send(method, params, timeout, self._verify)
        if _verify_mismatch(response, self._verify):
            self._verify = not self._verify
            await self._note(
                "This firmware " + ("asks for a verify flag." if self._verify else "rejects the verify flag.")
            )
            response = await self._send(method, params, timeout, self._verify)
        error = response_error(response)
        if error:
            self.state["last_error"] = f"{method}: {error}"
            if "guest" in error.lower() or "master" in error.lower():
                self.master = False
                self.state["master"] = False
            await self._emit("state", self.snapshot())
            raise ScopeError(self.state["last_error"], response)
        self._absorb(method, response.get("result"), response)
        if method != "test_connection":
            await self._emit("state", self.snapshot())
        return response

    async def _send(self, method: str, params: Any, timeout: float, verify: bool) -> dict[str, Any]:
        self._ensure()
        command_id = self._next_id
        self._next_id += 1
        message: dict[str, Any] = {"id": command_id, "method": method}
        body = _with_verify(params, verify)
        if body is not None:
            message["params"] = body
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        assert self._writer is not None
        self._writer.write(dumps(message))
        try:
            await self._writer.drain()
        except Exception as exc:
            self._pending.pop(command_id, None)
            raise ScopeError(f"Could not send {method}: {exc}") from exc
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(command_id, None)
            raise ScopeError(f"{method} timed out.") from exc

    def _ensure(self) -> None:
        if not self.connected or self._writer is None:
            raise ScopeError("The telescope is not connected.")

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while self.connected:
                data = await self._reader.read(65536)
                if not data:
                    break
                for message in self.lines.feed(data):
                    await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state["last_error"] = f"Connection lost: {exc}"
        finally:
            self.connected = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ScopeError("The telescope connection closed."))
            self._pending.clear()
            await self._emit("state", self.snapshot())

    async def _dispatch(self, message: dict[str, Any]) -> None:
        command_id = message.get("id")
        future = self._pending.get(command_id) if isinstance(command_id, int) else None
        if future is not None and not future.done():
            self._pending.pop(command_id, None)
            future.set_result(message)
        if "Event" in message or future is None:
            self._absorb_event(message)
            await self._emit("event", {"event": message, "state": self.snapshot()})

    async def _heartbeat(self) -> None:
        try:
            while self.connected:
                await asyncio.sleep(4)
                if not self.connected:
                    break
                try:
                    await self.command("test_connection", timeout=3)
                except ScopeError:
                    continue
        except asyncio.CancelledError:
            raise

    async def _poll(self) -> None:
        methods = ("scope_get_equ_coord", "scope_get_horiz_coord", "get_view_state", "get_device_state")
        index = 0
        try:
            while self.connected:
                await asyncio.sleep(2.5)
                if not self.connected:
                    break
                method = methods[index % len(methods)]
                index += 1
                try:
                    await self.command(method, timeout=4)
                except ScopeError:
                    continue
        except asyncio.CancelledError:
            raise

    def _absorb(self, method: str, result: Any, message: dict[str, Any]) -> None:
        if method == "get_device_state":
            _absorb_device(self.state, result)
        elif method in {"scope_get_equ_coord", "scope_get_ra_dec"}:
            _absorb_equatorial(self.state, result)
        elif method == "scope_get_horiz_coord":
            _absorb_horizontal(self.state, result)
        elif method == "get_disk_volume":
            _absorb_disk(self.state, result)
        elif method == "get_focuser_position":
            self.state["focus"] = _number(result)
        elif method == "get_wheel_position":
            self.state["filter"] = _number(result if not isinstance(result, dict) else result.get("position", result))
        elif method == "pi_station_state" and isinstance(result, dict):
            self.state["wifi"] = result.get("ssid") or self.state.get("wifi")
            self.state["station"] = result
        elif method == "get_setting" and isinstance(result, dict):
            self.state["settings"] = result
        elif method == "get_view_state":
            if isinstance(result, dict):
                self.state["view"] = result
                self.state["mode"] = result.get("mode") or self.state["mode"]
                self.state["target"] = result.get("target_name") or self.state["target"]
        elif method == "set_setting" and isinstance(result, dict):
            settings = dict(self.state.get("settings") or {})
            settings.update(result)
            self.state["settings"] = settings

    def _absorb_event(self, message: dict[str, Any]) -> None:
        name = str(message.get("Event") or "")
        if not name:
            return
        if name == "PiStatus":
            _absorb_device(self.state, message)
        elif name in {"AutoGoto", "View", "ContinuousExposure"}:
            view = dict(self.state.get("view") or {})
            view.update({key: value for key, value in message.items() if key != "Timestamp"})
            self.state["view"] = view
            if message.get("target_name"):
                self.state["target"] = message["target_name"]
            if message.get("mode"):
                self.state["mode"] = message["mode"]
        elif name in {"Stack", "Exposure"}:
            stack = dict(self.state.get("stack") or {})
            stack.update(message)
            self.state["stack"] = stack
        elif name in {"3PPA", "EqModePA"}:
            align = dict(self.state.get("align") or {})
            align.update(message)
            self.state["align"] = align
        elif name in {"FocuserMove", "AutoFocus"}:
            if "position" in message:
                self.state["focus"] = message["position"]
            self.state["focus_state"] = message.get("state")
        entry = {"time": time.strftime("%H:%M:%S"), "event": name, "state": message.get("state"), "detail": _brief(message)}
        self.log.append(entry)
        del self.log[:-200]

    async def _on_frame(self, camera: str, jpeg: bytes, frame_meta: dict[str, Any]) -> None:
        self.frames[camera] = jpeg
        self.frame_seq[camera] = self.frame_seq.get(camera, 0) + 1
        await self._emit(
            "frame",
            {"id": self.id, "camera": camera, "seq": self.frame_seq[camera], **frame_meta},
        )

    async def _note(self, text: str) -> None:
        self.log.append({"time": time.strftime("%H:%M:%S"), "event": "Desk", "state": "", "detail": text})
        del self.log[:-200]
        await self._emit("log", {"text": text, "state": self.snapshot()})

    async def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.on_update is None:
            return
        try:
            await self.on_update(kind, payload)
        except Exception:
            return


class ImageClient:
    def __init__(self, host: str, port: int, camera: str, on_jpeg) -> None:
        self.host = host
        self.port = port
        self.camera = camera
        self.on_jpeg = on_jpeg
        self.running = False
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._next_id = 1

    async def start(self) -> None:
        reader, self._writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), timeout=5)
        self.running = True
        self._task = asyncio.create_task(self._run(reader), name=f"image-{self.camera}")

    async def stop(self) -> None:
        self.running = False
        if self._writer is not None:
            try:
                await self._send("stop_streaming")
            except Exception:
                pass
            self._writer.close()
        if self._task:
            self._task.cancel()
            self._task = None

    async def _send(self, method: str) -> None:
        if self._writer is None:
            return
        message = {"id": self._next_id, "method": method}
        self._next_id += 1
        self._writer.write(dumps(message))
        await self._writer.drain()

    async def _run(self, reader: asyncio.StreamReader) -> None:
        parser = FrameParser()
        last_beat = time.monotonic()
        try:
            await self._send("begin_streaming")
            while self.running:
                if time.monotonic() - last_beat > 4:
                    await self._send("test_connection")
                    last_beat = time.monotonic()
                data = await asyncio.wait_for(reader.read(262144), timeout=8)
                if not data:
                    break
                for kind, item in parser.feed(data):
                    if kind != "frame":
                        continue
                    jpeg = await asyncio.get_running_loop().run_in_executor(None, to_jpeg, item)
                    if jpeg:
                        await self.on_jpeg(
                            self.camera,
                            jpeg,
                            {"img_type": item.img_type, "width": item.width, "height": item.height},
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.running = False


def response_error(message: dict[str, Any]) -> str | None:
    if not isinstance(message, dict):
        return "empty response"
    error = message.get("error")
    code = message.get("code", 0)
    if isinstance(error, dict):
        code = error.get("code", code)
        text = error.get("message") or error.get("error") or str(error)
        if code not in (0, None):
            return f"{code}: {text}"
    elif error not in (None, "", 0, False, "ok"):
        return f"{code}: {error}"
    if code not in (0, None):
        detail = message.get("message") or message.get("result") or "command failed"
        return f"{code}: {detail}"
    return None


def _unsupported(exc: ScopeError) -> bool:
    response = exc.response or {}
    if response.get("code") == 210:
        return True
    return "not supported" in str(exc).lower()


def _verify_mismatch(message: dict[str, Any], sent_verify: bool) -> bool:
    text = response_error(message) or ""
    if "verify" not in text.lower():
        return False
    return True


def _with_verify(params: Any, verify: bool) -> Any:
    if not verify:
        return params
    if params is None:
        return {"verify": True}
    if isinstance(params, dict):
        body = dict(params)
        body["verify"] = True
        return body
    return params


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        for key in ("position", "step", "value", "focuser_position"):
            if isinstance(value.get(key), (int, float)):
                return value[key]
    if isinstance(value, list) and value and isinstance(value[0], (int, float)):
        return value[0]
    return None


def _absorb_equatorial(state: dict[str, Any], result: Any) -> None:
    if isinstance(result, dict):
        if "ra" in result:
            state["ra"] = result["ra"]
        if "dec" in result:
            state["dec"] = result["dec"]
    elif isinstance(result, list) and len(result) >= 2:
        state["ra"], state["dec"] = result[0], result[1]


def _absorb_horizontal(state: dict[str, Any], result: Any) -> None:
    if isinstance(result, dict):
        state["alt"] = result.get("alt", result.get("altitude", state.get("alt")))
        state["az"] = result.get("az", result.get("azimuth", state.get("az")))
    elif isinstance(result, list) and len(result) >= 2:
        state["alt"], state["az"] = result[0], result[1]


def _absorb_disk(state: dict[str, Any], result: Any) -> None:
    if isinstance(result, dict):
        for key, target in (("free", "disk_free"), ("free_mb", "disk_free"), ("avail", "disk_free")):
            if key in result:
                state[target] = result[key]
        for key, target in (("total", "disk_total"), ("total_mb", "disk_total"), ("capacity", "disk_total")):
            if key in result:
                state[target] = result[key]
    elif isinstance(result, list) and len(result) >= 2:
        state["disk_total"], state["disk_free"] = result[0], result[1]


def _absorb_device(state: dict[str, Any], result: Any) -> None:
    if not isinstance(result, dict):
        return
    device = result.get("device") if isinstance(result.get("device"), dict) else result
    if isinstance(device, dict):
        if device.get("firmware_ver_int"):
            state["firmware"] = device["firmware_ver_int"]
        for key in ("name", "device_name"):
            if device.get(key):
                state["name"] = device[key]
        for key in ("model", "product_model"):
            if device.get(key):
                state["model"] = device[key]
        if "battery_capacity" in device:
            state["battery"] = device["battery_capacity"]
        if "battery" in device and not isinstance(device["battery"], dict):
            state["battery"] = device["battery"]
    for key in ("battery_capacity", "battery"):
        if key in result and not isinstance(result[key], dict):
            state["battery"] = result[key]
    if "charge_online" in result:
        state["charging"] = bool(result["charge_online"])
    if "temp" in result:
        state["temperature"] = result["temp"]
    mount = result.get("mount") if isinstance(result.get("mount"), dict) else {}
    if "equ_mode" in mount:
        state["equatorial"] = bool(mount["equ_mode"])
    if "close" in mount:
        state["parked"] = bool(mount["close"])
    if "track" in mount:
        state["tracking"] = bool(mount["track"])
    storage = result.get("storage") if isinstance(result.get("storage"), dict) else None
    if storage:
        _absorb_disk(state, storage)


def _brief(message: dict[str, Any]) -> str:
    parts = []
    for key in ("state", "error", "target_name", "mode", "state_code", "stack_count"):
        if key in message and key != "state":
            parts.append(f"{key} {message[key]}")
    if message.get("error"):
        parts.insert(0, str(message["error"]))
    return ", ".join(str(part) for part in parts[:4])
