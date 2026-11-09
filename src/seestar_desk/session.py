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
from seestar_desk.imaging import ACK, JPEG, RAW, FrameParser, preview_and_display
from seestar_desk.plan import NightPlan

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
    "pi_station_scan",
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
            "live_error": "",
            "mode": "",
            "target": "",
            "latitude": None,
            "longitude": None,
        }
        self.log: list[dict[str, Any]] = []
        self.frames: dict[str, bytes] = {}
        self.display_frames: dict[str, bytes] = {}
        self.frame_seq: dict[str, int] = {"tele": 0, "wide": 0, "stack": 0}
        self._streams: dict[str, ImageClient] = {}
        self._video_task: asyncio.Task | None = None
        self._last_stacked_pull: Any = None
        self._frame_at: dict[str, float] = {}
        self._preview_at = 0.0
        self.night = NightPlan(self)

    @property
    def id(self) -> str:
        return self.found.id

    def snapshot(self) -> dict[str, Any]:
        snap = dict(self.state)
        snap["connected"] = self.connected
        snap["master"] = self.master
        snap["frame_seq"] = dict(self.frame_seq)
        stack = dict(snap.get("stack") or {})
        phase = str(stack.get("state") or "")
        started = stack.get("started_at")
        if started and phase in {"start", "working", "stacking"} and stack.get("elapsed") in (None, ""):
            stack["elapsed"] = max(0, int(time.time() - float(started)))
        snap["stack"] = stack
        snap["plan"] = self.night.public()
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
        self.night.stop()
        if self.night._task and not self.night._task.done():
            self.night._task.cancel()
        if self._video_task and not self._video_task.done():
            self._video_task.cancel()
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
            "get_camera_info",
            "pi_station_state",
            "get_user_location",
            "pi_get_time",
        ):
            try:
                await self.command(method, timeout=4)
            except ScopeError as exc:
                if method in {"get_user_location", "pi_get_time"}:
                    continue
                self.state["last_error"] = str(exc)
                break

    async def goto(self, ra: float | None, dec: float | None, name: str, mode: str, lp_filter: bool) -> dict[str, Any]:
        params: dict[str, Any] = {"mode": mode or "star", "target_name": name or "Target", "lp_filter": lp_filter}
        if ra is not None and dec is not None and mode in {"star", ""}:
            params["target_ra_dec"] = [ra, dec]
        self._note_target(params["target_name"])
        response = await self.command("iscope_start_view", params)
        self.state["mode"] = params["mode"]
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
        previous = dict(self.state.get("stack") or {})
        stack = dict(previous)
        stack["state"] = "working"
        if not stack.get("started_at"):
            stack["started_at"] = time.time()
        self.state["stack"] = stack
        self._last_stacked_pull = None
        await self._quiet_preview()
        try:
            response = await self.command("iscope_start_stack", {"restart": restart})
        except ScopeError:
            self.state["stack"] = previous
            raise
        phase = str((self.state.get("stack") or {}).get("state") or "").lower()
        if phase not in {"start", "working", "stacking", "complete", "cancel", "fail", "stopped", "stop", "idle"}:
            current = dict(self.state.get("stack") or {})
            current["state"] = "working"
            self.state["stack"] = current
        if gain is not None:
            try:
                await self.command("set_control_value", ["gain", int(gain)])
            except ScopeError as exc:
                self.state["last_error"] = str(exc)
        try:
            await self._watch_stack_frames()
        except (ScopeError, OSError, asyncio.TimeoutError) as exc:
            self.state["live_error"] = str(exc)
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
            self.state["latitude"] = float(payload["lat"])
            self.state["longitude"] = float(payload["lon"])
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

    async def stop_images(self) -> None:
        clients = list(self._streams.values())
        self._streams.clear()
        for client in clients:
            await client.stop()

    async def park(self) -> dict[str, Any]:
        await self.stop_images()
        response = await self.command("scope_park")
        self.state["parked"] = True
        return response

    async def set_mount_mode(self, equatorial: bool) -> dict[str, Any]:
        await self.stop_images()
        await self.command("scope_move_to_horizon")
        response = await self.command("scope_park", {"equ_mode": bool(equatorial)})
        self.state["parked"] = True
        return response

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
        if enabled and self.state.get("parked"):
            await self.stop_images()
            return
        current = self._streams.get(camera)
        if not enabled:
            if current:
                await current.stop()
                self._streams.pop(camera, None)
            return
        if self._exposure_busy():
            if camera == "tele":
                await self._watch_stack_frames()
            return
        if (
            self.state.get("camera") == camera
            and current
            and current.running
            and time.time() - self._frame_at.get(camera, 0) < 2
        ):
            return
        previous = self.state.get("camera")
        self.state["camera"] = camera
        other = self._streams.pop("tele" if camera == "wide" else "wide", None)
        if other:
            await other.stop()
        if camera == "wide" or previous == "wide":
            await self._engage_camera(camera)
        if current and current.running:
            if time.time() - self._frame_at.get(camera, 0) < 2:
                return
            await self._ensure_preview(force=True)
            await current.request("begin_streaming")
            return
        await self._ensure_preview(force=True)
        port = self.wide_port if camera == "wide" else self.image_port
        client = ImageClient(
            self.host, port, camera, self._on_frame, self._on_live_status, self._preview_for_stream, preview=True
        )
        self._streams[camera] = client
        try:
            await client.start()
        except Exception:
            self._streams.pop(camera, None)
            raise
        self.state["live_error"] = ""

    def _note_target(self, name: Any) -> None:
        cleaned = str(name or "").strip()
        if not cleaned or cleaned.casefold() == "preview":
            return
        previous = str(self.state.get("target") or "").strip()
        if previous.casefold() == cleaned.casefold():
            return
        self.state["target"] = cleaned
        if previous and previous.casefold() != "preview":
            self._clear_stack_picture()

    def _clear_stack_picture(self) -> None:
        self.frames.pop("stack", None)
        self.display_frames.pop("stack", None)
        self.frame_seq["stack"] = 0
        self._last_stacked_pull = None
        stack = dict(self.state.get("stack") or {})
        for key in ("stacked", "dropped", "total", "elapsed", "state", "started_at"):
            stack.pop(key, None)
        self.state["stack"] = stack

    def _exposure_busy(self) -> bool:
        phase = str((self.state.get("stack") or {}).get("state") or "").lower()
        if phase in {"start", "working", "stacking"}:
            return True
        stage = str((self.state.get("view") or {}).get("stage") or "").lower().replace("_", "")
        return stage == "stack" or ("dark" in stage and "lib" in stage)

    async def _quiet_preview(self) -> None:
        """Stop asking for a live picture so it cannot replace the open exposure."""
        for client in self._streams.values():
            client.preview = False
            if client.running:
                await client.request("stop_streaming")

    async def _watch_stack_frames(self) -> None:
        client = self._streams.get("tele")
        if client is None or not client.running:
            client = ImageClient(
                self.host,
                self.image_port,
                "tele",
                self._on_frame,
                self._on_live_status,
                self._preview_for_stream,
                preview=False,
            )
            self._streams["tele"] = client
            try:
                await client.start()
            except Exception:
                self._streams.pop("tele", None)
                raise
        client.preview = False
        await client.request("get_stacked_img")

    async def _engage_camera(self, camera: str) -> None:
        """Open the telephoto or wide-angle sensor where the telescope is already pointing.

        Naming the exposure stage does not stop it on this firmware, and a camera
        change is ignored while that exposure is open. An empty stop does end it.
        The wide lens is the second sensor, selected on the new view.
        """
        try:
            await self.command("iscope_stop_view", {})
        except ScopeError as exc:
            if not _unsupported(exc):
                self.state["live_error"] = str(exc)
        await asyncio.sleep(0.4)
        params: dict[str, Any] = {"mode": "star", "target_name": "Preview"}
        if camera == "wide":
            params["selected_cam"] = "SecondView"
        try:
            await self.command("iscope_start_view", params)
        except ScopeError as exc:
            self.state["live_error"] = str(exc)
            return
        self.state["last_error"] = ""
        self.state["live_error"] = ""
        self._preview_at = time.time()

    async def _preview_for_stream(self) -> None:
        await self._ensure_preview(force=True)

    async def _ensure_preview(self, force: bool = False) -> None:
        """Open the camera where it is pointing so the image port will send frames."""
        if self._exposure_busy():
            return
        try:
            await self.command("get_view_state", timeout=4)
        except ScopeError:
            pass
        stage = str((self.state.get("view") or {}).get("stage") or "")
        view_state = str((self.state.get("view") or {}).get("state") or "")
        if self._exposure_busy() or stage == "RTSP" or (stage == "AutoGoto" and view_state == "working"):
            return
        # The wide sensor reports its view as Sleep while it is exposing.
        if view_state == "working" and stage not in {"", "none", "ContinuousExposure"}:
            return
        if stage == "ContinuousExposure" and (not force or time.time() - self._preview_at < 4):
            return
        self._preview_at = time.time()
        params: dict[str, Any] = {
            "mode": self.state.get("mode") or "star",
            "target_name": self.state.get("target") or "Preview",
        }
        if self.state.get("camera") == "wide":
            params["selected_cam"] = "SecondView"
        await self.command("iscope_start_view", params)

    async def start_video(self, mode: str, minutes: float | None) -> dict[str, Any]:
        mode = mode if mode in {"planet", "moon", "scenery"} else "planet"
        response = await self.command(
            "iscope_start_view",
            {"mode": mode, "target_name": mode.title()},
        )
        if mode == "planet":
            try:
                await self.command("start_scan_planet")
            except ScopeError as exc:
                if not _unsupported(exc):
                    raise
        self.state["mode"] = mode
        self.state["target"] = mode.title()
        try:
            await self.live("tele", True)
        except (ScopeError, OSError, asyncio.TimeoutError) as exc:
            self.state["live_error"] = str(exc)
        if self._video_task and not self._video_task.done():
            self._video_task.cancel()
        self._video_task = None
        if minutes is not None:
            self._video_task = asyncio.create_task(self._end_video(max(0.0, float(minutes))))
        return response

    async def _end_video(self, minutes: float) -> None:
        try:
            await asyncio.sleep(minutes * 60.0)
            await self.stop()
            await self._note(f"Recording stopped after {minutes:g} min.")
        except asyncio.CancelledError:
            raise

    async def wifi_scan(self) -> list[dict[str, Any]]:
        response = await self.command("pi_station_scan", timeout=12)
        return _networks(response.get("result"))

    async def wifi_join(self, ssid: str, password: str) -> dict[str, Any]:
        if not ssid.strip():
            raise ScopeError("Enter the network name.")
        await self.command("pi_station_set", {"ssid": ssid, "passwd": password}, timeout=15)
        try:
            await self.command("pi_station_open", timeout=8)
        except ScopeError as exc:
            if not _unsupported(exc):
                raise
        try:
            await self.command("pi_station_state", timeout=8)
        except ScopeError:
            pass
        await self._note(f"Asked the telescope to join {ssid}.")
        return self.snapshot()

    async def delete_images(self, folder: str, names: list[str]) -> list[str]:
        from seestar_desk.album import delete_files

        return await delete_files(self, folder, names)

    def file_url(self, path: str) -> str:
        quoted = urllib.parse.quote(path, safe="/")
        return f"http://{self.host}:{self.http_port}/{quoted}"

    async def _set_clock(self) -> dict[str, Any]:
        now = time.localtime()
        zone = time.tzname[1] if time.daylight and now.tm_isdst else time.tzname[0]
        self.state["clock"] = time.strftime("%Y-%m-%d %H:%M:%S", now)
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
        others = ("scope_get_equ_coord", "scope_get_horiz_coord", "get_view_state")
        index = 0
        try:
            while self.connected:
                await asyncio.sleep(2.5)
                if not self.connected:
                    break
                # The level and compass live in the device state, so read that every pass.
                for method in ("get_device_state", others[index % len(others)]):
                    index += 1 if method != "get_device_state" else 0
                    try:
                        await self.command(method, timeout=4)
                    except ScopeError:
                        continue
        except asyncio.CancelledError:
            raise

    def _watch_park(self, before: Any) -> None:
        if self.state.get("parked") and not before and self._streams:
            asyncio.get_running_loop().create_task(self.stop_images())

    def _absorb(self, method: str, result: Any, message: dict[str, Any]) -> None:
        before = self.state.get("parked")
        if method == "get_device_state":
            _absorb_device(self.state, result)
            self._watch_park(before)
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
            _absorb_station(self.state, result)
        elif method in {"get_user_location", "set_user_location"}:
            _absorb_location(self.state, result)
        elif method == "pi_get_time":
            text = _clock_text(result)
            if text:
                self.state["clock"] = text
        elif method == "get_camera_info" and isinstance(result, dict):
            pattern = result.get("debayer_pattern")
            if pattern:
                self.state["debayer"] = str(pattern)
        elif method == "get_setting" and isinstance(result, dict):
            self.state["settings"] = result
        elif method == "get_view_state":
            if isinstance(result, dict):
                nested = result.get("View") if isinstance(result.get("View"), dict) else {}
                flat = {**result, **nested}
                view = dict(self.state.get("view") or {})
                view.update(flat)
                self.state["view"] = view
                self.state["mode"] = flat.get("mode") or self.state["mode"]
                self._note_target(flat.get("target_name"))
        elif method == "set_setting" and isinstance(result, dict):
            settings = dict(self.state.get("settings") or {})
            settings.update(result)
            self.state["settings"] = settings

    def _absorb_event(self, message: dict[str, Any]) -> None:
        name = str(message.get("Event") or "")
        if not name:
            return
        before = self.state.get("parked")
        if name == "PiStatus":
            _absorb_device(self.state, message)
            self._watch_park(before)
        elif name in {"AutoGoto", "View", "ContinuousExposure", "DarkLibrary"}:
            if name == "DarkLibrary" and "stage" not in message:
                message = {**message, "stage": "DarkLibrary"}
            _absorb_view(self.state, message)
            self._note_target(message.get("target_name"))
        elif name in {"Stack", "Exposure"}:
            _absorb_stack(self.state, message)
            self._pull_stack_frame()
        elif name in {"3PPA", "EqModePA"}:
            align = dict(self.state.get("align") or {})
            for key in ("x", "y", "state", "state_code", "error", "Event", "eq_offset_alt", "eq_offset_az"):
                if key in message:
                    align[key] = message[key]
            if "x" not in align and "eq_offset_az" in align:
                align["x"] = align["eq_offset_az"]
            if "y" not in align and "eq_offset_alt" in align:
                align["y"] = align["eq_offset_alt"]
            self.state["align"] = align
        elif name in {"FocuserMove", "AutoFocus"}:
            if "position" in message:
                self.state["focus"] = message["position"]
            self.state["focus_state"] = message.get("state")
        entry = {"time": time.strftime("%H:%M:%S"), "event": name, "state": message.get("state"), "detail": _brief(message)}
        self.log.append(entry)
        del self.log[:-200]

    def _pull_stack_frame(self) -> None:
        client = self._streams.get("tele")
        stacked = (self.state.get("stack") or {}).get("stacked")
        if client is None or not client.running or stacked in (None, self._last_stacked_pull):
            return
        self._last_stacked_pull = stacked
        asyncio.create_task(client.request("get_stacked_img"))

    async def _on_live_status(self, text: str) -> None:
        self.state["live_error"] = text
        await self._emit("state", self.snapshot())

    async def _on_frame(self, camera: str, jpeg: bytes, frame_meta: dict[str, Any], display: bytes | None = None) -> None:
        self._frame_at[camera] = time.time()
        self.state["live_error"] = ""
        self.frames[camera] = jpeg
        if display:
            self.display_frames[camera] = display
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
    def __init__(self, host: str, port: int, camera: str, on_jpeg, on_status=None, on_need_preview=None, preview: bool = True) -> None:
        self.host = host
        self.port = port
        self.camera = camera
        self.on_jpeg = on_jpeg
        self.on_status = on_status
        self.on_need_preview = on_need_preview
        self.preview = preview
        self.running = False
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._next_id = 1
        self._out: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._retry_stream = False
        self._saw_frame = False

    async def request(self, method: str, params: Any = None) -> None:
        await self._out.put((method, params))

    async def start(self) -> None:
        reader, self._writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), timeout=5)
        sock = self._writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
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

    async def _send(self, method: str, params: Any = None) -> None:
        if self._writer is None:
            return
        message: dict[str, Any] = {"id": self._next_id, "method": method}
        self._next_id += 1
        if params is not None:
            message["params"] = params
        self._writer.write(dumps(message))
        await self._writer.drain()

    async def _run(self, reader: asyncio.StreamReader) -> None:
        parser = FrameParser()
        last_beat = time.monotonic()
        last_pull = 0.0
        try:
            await self._handshake(parser, reader)
            if self.preview:
                await self._send("begin_streaming")
            while self.running:
                while not self._out.empty():
                    method, params = self._out.get_nowait()
                    await self._send(method, params)
                now = time.monotonic()
                if now - last_beat > 4:
                    await self._send("test_connection")
                    last_beat = now
                if self.preview and now - last_pull > 3 and not self._saw_frame:
                    await self._send("get_current_img")
                    last_pull = now
                if self.preview and self._retry_stream:
                    self._retry_stream = False
                    await self._send("begin_streaming")
                try:
                    data = await asyncio.wait_for(reader.read(262144), timeout=1)
                except asyncio.TimeoutError:
                    continue
                if not data:
                    break
                await self._consume(parser.feed(data))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.running = False
            if self.on_status:
                await self.on_status(str(exc))

    async def _handshake(self, parser: FrameParser, reader: asyncio.StreamReader) -> None:
        """Sign the image port when it asks. Current firmware answers with a binary note instead."""
        await self._send("get_verify_str", "verify")
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline and self.running:
            try:
                data = await asyncio.wait_for(reader.read(262144), timeout=max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                return
            if not data:
                return
            message: dict[str, Any] | None = None
            frames = []
            for kind, item in parser.feed(data):
                if kind == "json" and message is None:
                    message = item
                elif kind == "frame":
                    frames.append(item)
            await self._consume([("frame", item) for item in frames])
            if message is None:
                if any(item.img_type == ACK for item in frames):
                    return
                continue
            error = response_error(message) or ""
            if error.startswith("103") or "unknown" in error.lower() or "not found" in error.lower():
                return
            result = message.get("result") if isinstance(message.get("result"), dict) else {}
            challenge = result.get("str") or result.get("verify_str")
            if not challenge:
                return
            key = find_key()
            if key is None:
                raise ScopeError(MISSING_KEY)
            await self._send("verify_client", {"sign": sign_challenge(key, str(challenge)), "data": str(challenge)})
            await self._next_json(parser, reader, 4)
            return

    async def _next_json(self, parser: FrameParser, reader: asyncio.StreamReader, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.running:
            try:
                data = await asyncio.wait_for(reader.read(262144), timeout=max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                return None
            if not data:
                return None
            found: dict[str, Any] | None = None
            frames = []
            for kind, item in parser.feed(data):
                if kind == "json" and found is None:
                    found = item
                elif kind == "frame":
                    frames.append(item)
            await self._consume([("frame", item) for item in frames])
            if found is not None:
                return found
        return None

    async def _consume(self, items: list[tuple[str, Any]]) -> None:
        loop = asyncio.get_running_loop()
        for kind, item in items:
            if kind == "json":
                error = response_error(item)
                if error and item.get("method") not in {None, "test_connection"} and self.on_status:
                    await self.on_status(error)
                continue
            jpeg, display = await loop.run_in_executor(None, preview_and_display, item)
            if item.img_type == ACK:
                note = item.payload.decode("latin1", "replace").strip()
                lowered = note.lower()
                if "continuous exposure" in lowered and self.preview:
                    if self.on_need_preview:
                        await self.on_need_preview()
                    self._retry_stream = True
                elif note and not _quiet_image_note(lowered):
                    if self.on_status:
                        await self.on_status(note)
                continue
            if not jpeg:
                continue
            self._saw_frame = True
            role = "stack" if item.img_type in {JPEG, RAW} else self.camera
            await self.on_jpeg(
                role,
                jpeg,
                {"img_type": item.img_type, "width": item.width, "height": item.height},
                display,
            )


def _coord(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _absorb_location(state: dict[str, Any], result: Any) -> None:
    lat = lon = None
    if isinstance(result, dict):
        pair = result.get("location_lon_lat")
        if isinstance(pair, list):
            _absorb_location(state, pair)
        for key in ("location", "user_location"):
            nested = result.get(key)
            if isinstance(nested, (dict, list)):
                _absorb_location(state, nested)
        lat = result.get("lat", result.get("latitude"))
        lon = result.get("lon", result.get("longitude", result.get("lng", result.get("longtitude"))))
    elif isinstance(result, list) and len(result) >= 2:
        # The telescope sends [longitude, latitude].
        lon, lat = result[0], result[1]
    parsed_lat = _coord(lat)
    parsed_lon = _coord(lon)
    if parsed_lat is not None:
        state["latitude"] = parsed_lat
    if parsed_lon is not None:
        state["longitude"] = parsed_lon


def _clock_text(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    try:
        return (
            f"{int(result['year']):04d}-{int(result['mon']):02d}-{int(result['day']):02d} "
            f"{int(result['hour']):02d}:{int(result['min']):02d}:{int(result.get('sec') or 0):02d}"
        )
    except (KeyError, TypeError, ValueError):
        return ""


def _absorb_view(state: dict[str, Any], message: dict[str, Any]) -> None:
    view = dict(state.get("view") or {})
    for key in ("state", "stage", "target_name", "mode", "percent", "error", "state_code", "Event"):
        if key in message:
            view[key] = message[key]
    state["view"] = view
    if message.get("mode"):
        state["mode"] = message["mode"]


def _absorb_stack(state: dict[str, Any], message: dict[str, Any]) -> None:
    stack = dict(state.get("stack") or {})
    nested = message.get("stack_status") if isinstance(message.get("stack_status"), dict) else {}
    data = {key: value for key, value in message.items() if key != "Timestamp"}
    data.update(nested)
    extracted: dict[str, Any] = {}
    for src, dest in (
        ("stacked_frame", "stacked"),
        ("stacked_frames", "stacked"),
        ("stack_count", "stacked"),
        ("dropped_frame", "dropped"),
        ("dropped_frames", "dropped"),
        ("dropped_count", "dropped"),
        ("total_frame", "total"),
        ("total_frames", "total"),
    ):
        if src in data and data[src] is not None and dest not in extracted:
            extracted[dest] = data[src]
    stack.update(extracted)
    if data.get("elapsed") is not None:
        stack["elapsed"] = data["elapsed"]
    if data.get("state"):
        phase = str(data["state"])
        stack["state"] = phase
        if phase in {"start", "working", "stacking"} and not stack.get("started_at"):
            stack["started_at"] = time.time()
    state["stack"] = stack


def _networks(result: Any) -> list[dict[str, Any]]:
    rows = result if isinstance(result, list) else []
    if isinstance(result, dict):
        for key in ("list", "ssids", "stations", "aps"):
            if isinstance(result.get(key), list):
                rows = result[key]
                break
    networks: list[dict[str, Any]] = []
    for item in rows:
        if isinstance(item, str):
            networks.append({"ssid": item, "rssi": None})
        elif isinstance(item, dict) and (item.get("ssid") or item.get("name")):
            networks.append({"ssid": item.get("ssid") or item.get("name"), "rssi": item.get("rssi") or item.get("sig_lev")})
    return networks


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


def _quiet_image_note(text: str) -> bool:
    """Status text from the image port that is not a problem to show."""
    if text in {"done", "ok", "server connected!"} or "unknown met" in text or "unknown moth" in text:
        return True
    return "no image" in text


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


def _firmware_label(device: dict[str, Any]) -> str:
    for key in ("firmware_ver_string", "firmware_version", "fw_ver"):
        if device.get(key):
            return str(device[key])
    value = device.get("firmware_ver_int")
    text = str(value or "")
    if len(text) == 4 and text.startswith("2"):
        return f"{int(text[1:-2])}.{text[-2:]}"
    return text


def _absorb_power(state: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("battery_capacity", "battery_percent", "battery"):
        value = source.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            state["battery"] = value
            break
    if "charge_online" in source:
        state["charging"] = bool(source["charge_online"])
    status = str(source.get("charger_status") or "").lower()
    if status:
        state["charging"] = status not in {"discharging", "not charging", "idle", "none", "full"}


def _absorb_temperature(state: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("temp", "temperature", "pi_temp", "cpu_temp", "core_temp"):
        value = source.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            state["temperature"] = value
            return


def _absorb_station(state: dict[str, Any], result: dict[str, Any]) -> None:
    state["wifi"] = result.get("ssid") or state.get("wifi")
    state["station"] = result
    for key in ("ip", "station_ip", "addr", "ip_address"):
        if result.get(key):
            state["ip"] = str(result[key])
            break
    for key in ("rssi", "sig_lev", "signal", "quality"):
        value = result.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            state["wifi_signal"] = value
            return


def _absorb_disk(state: dict[str, Any], result: Any) -> None:
    if isinstance(result, dict):
        volume = _storage_volume(result)
        source = volume or result
        for key in ("free", "free_mb", "freeMB", "avail"):
            if isinstance(source.get(key), (int, float)) and not isinstance(source.get(key), bool):
                state["disk_free"] = source[key]
                break
        for key in ("total", "total_mb", "totalMB", "disk_mb", "diskSizeMB", "capacity"):
            if isinstance(source.get(key), (int, float)) and not isinstance(source.get(key), bool):
                state["disk_total"] = source[key]
                break
    elif isinstance(result, list) and len(result) >= 2 and all(isinstance(item, (int, float)) for item in result[:2]):
        state["disk_total"], state["disk_free"] = result[0], result[1]
    elif isinstance(result, list):
        for item in result:
            if isinstance(item, dict):
                _absorb_disk(state, item)
                return


def _storage_volume(result: dict[str, Any]) -> dict[str, Any] | None:
    volumes = result.get("storage_volume") or result.get("connected_storage")
    if not isinstance(volumes, list):
        return None
    current = str(result.get("cur_storage") or "")
    chosen: dict[str, Any] | None = None
    for item in volumes:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("path") or item.get("id") or item.get("type") or "")
        if current and current == name:
            return item
        chosen = chosen or item
    return chosen


def _sensor_data(block: Any) -> dict[str, Any] | None:
    if not isinstance(block, dict):
        return None
    data = block.get("data") if isinstance(block.get("data"), dict) else block
    if not isinstance(data, dict):
        return None
    kept: dict[str, Any] = {}
    for key in ("x", "y", "z", "angle", "direction"):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            kept[key] = value
    return kept or None


def _absorb_device(state: dict[str, Any], result: Any) -> None:
    if not isinstance(result, dict):
        return
    device = result.get("device") if isinstance(result.get("device"), dict) else result
    if isinstance(device, dict):
        if device.get("firmware_ver_int"):
            state["firmware"] = device["firmware_ver_int"]
        label = _firmware_label(device)
        if label:
            state["firmware_label"] = label
        for key in ("name", "device_name"):
            if device.get(key):
                state["name"] = device[key]
        for key in ("model", "product_model"):
            if device.get(key):
                state["model"] = device[key]
        _absorb_power(state, device)
        _absorb_temperature(state, device)
    _absorb_power(state, result)
    _absorb_temperature(state, result)
    pi_status = result.get("pi_status") if isinstance(result.get("pi_status"), dict) else None
    if pi_status:
        _absorb_power(state, pi_status)
        _absorb_temperature(state, pi_status)
    mount = result.get("mount") if isinstance(result.get("mount"), dict) else {}
    if "equ_mode" in mount:
        state["equatorial"] = bool(mount["equ_mode"])
    if "close" in mount:
        state["parked"] = bool(mount["close"])
    if "track" in mount:
        state["tracking"] = bool(mount["track"])
    level = _sensor_data(result.get("balance_sensor"))
    if level:
        state["level"] = level
    heading = _sensor_data(result.get("compass_sensor"))
    if heading and isinstance(heading.get("direction"), (int, float)) and not isinstance(heading.get("direction"), bool):
        # The sensor's zero is 90° clockwise from the direction the scope points.
        # Facing true north reports about 90°.
        state["heading"] = (float(heading["direction"]) - 90.0) % 360.0
    if "location_lon_lat" in result:
        _absorb_location(state, result["location_lon_lat"])
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
