"""HTTP and WebSocket server for the desk."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

mimetypes.add_type("application/wasm", ".wasm")
from pathlib import Path
from typing import Any

from aiohttp import web

from seestar_desk.album import download_files, list_albums, list_folder
from seestar_desk.catalog import search
from seestar_desk.plan import forecast_at
from seestar_desk.sky import best_tonight, darkness, sun_altitude
from seestar_desk.discovery import FoundScope, discover
from seestar_desk.errors import ScopeError
from seestar_desk.session import ScopeSession

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"


class Desk:
    def __init__(self) -> None:
        self.sessions: dict[str, ScopeSession] = {}
        self.known: dict[str, FoundScope] = {}
        self.sockets: set[web.WebSocketResponse] = set()
        self.demo_only = False
        self.place: dict[str, float] | None = None
        self.path = Path.home() / ".seestar-desk" / "scopes.json"
        self.downloads = Path.home() / "Pictures" / "Seestar Desk"
        self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        place = saved.get("place") if isinstance(saved, dict) else None
        if isinstance(place, dict):
            self.remember_place(place.get("latitude"), place.get("longitude"), store=False)
        for item in saved.get("scopes") or []:
            found = FoundScope(
                host=item["host"],
                name=item.get("name") or "",
                model=item.get("model") or "",
                ssid=item.get("ssid") or "",
            )
            # Scopes saved before this flag had already been kept.
            found.raw["linked"] = bool(item["linked"]) if "linked" in item else True
            self.known[found.id] = found

    def _save(self) -> None:
        scopes = [
            {
                "host": item.host,
                "name": item.name,
                "model": item.model,
                "ssid": item.ssid,
                "linked": bool(item.raw.get("linked")),
            }
            for item in self.known.values()
            if not item.raw.get("demo") and item.control_port == 4700
        ]
        body: dict[str, Any] = {"scopes": scopes}
        if self.place:
            body["place"] = self.place
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(body, indent=2), encoding="utf-8")

    def remember_place(self, latitude: Any, longitude: Any, store: bool = True) -> dict[str, float] | None:
        try:
            lat = float(latitude)
            lon = float(longitude)
        except (TypeError, ValueError):
            return None
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            return None
        place = {"latitude": round(lat, 4), "longitude": round(lon, 4)}
        if self.place == place:
            return place
        self.place = place
        if store:
            self._save()
        return place

    def pin(self, found: FoundScope) -> None:
        self.known[found.id] = found

    async def scan(self) -> list[dict[str, Any]]:
        if not self.demo_only:
            for found in await discover():
                current = self.known.get(found.id)
                if current and current.raw.get("demo"):
                    continue
                if current:
                    found.control_port = current.control_port
                    found.image_port = current.image_port
                    found.wide_port = current.wide_port
                    found.http_port = current.http_port
                    if current.raw.get("linked"):
                        found.raw["linked"] = True
                    if not found.model and current.model:
                        found.model = current.model
                    if not found.name and current.name:
                        found.name = current.name
                self.known[found.id] = found
            self._save()
        return [self.describe(item) for item in self.known.values()]

    def describe(self, found: FoundScope) -> dict[str, Any]:
        session = self.sessions.get(found.id)
        return {
            "id": found.id,
            "host": found.host,
            "name": found.name,
            "model": found.model,
            "ssid": found.ssid,
            "connected": bool(session and session.connected),
            "demo": bool(found.raw.get("demo")),
            "linked": bool(found.raw.get("linked")) or bool(session and session.connected),
        }

    def add_host(self, host: str, name: str = "") -> FoundScope:
        found = FoundScope(host=host.strip(), name=name.strip())
        if not found.host:
            raise ScopeError("Enter the telescope's IP address.")
        self.known[found.id] = found
        self._save()
        return found

    async def connect(self, scope_id: str) -> dict[str, Any]:
        found = self.known.get(scope_id)
        if found is None:
            raise ScopeError("That telescope is not in the list yet.")
        session = self.sessions.get(scope_id)
        if session is None or not session.connected:
            session = ScopeSession(found)
            session.on_update = self._on_update
            self.sessions[scope_id] = session
            await session.connect()
        found.raw["linked"] = True
        model = session.state.get("model")
        if isinstance(model, str) and model:
            found.model = model
        self._save()
        return session.snapshot()

    async def session(self, scope_id: str) -> ScopeSession:
        current = self.sessions.get(scope_id)
        if current is None or not current.connected:
            raise ScopeError("Connect the telescope first.")
        return current

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale: list[web.WebSocketResponse] = []
        text = json.dumps(payload, default=str)
        for socket in self.sockets:
            try:
                await socket.send_str(text)
            except Exception:
                stale.append(socket)
        for socket in stale:
            self.sockets.discard(socket)

    async def _on_update(self, kind: str, payload: dict[str, Any]) -> None:
        self._remember_payload(payload)
        message = {"type": kind}
        message.update(payload)
        await self.broadcast(message)

    def _remember_payload(self, payload: dict[str, Any]) -> None:
        self.remember_place(payload.get("latitude"), payload.get("longitude"))
        self._remember_scope(payload)
        nested = payload.get("state")
        if isinstance(nested, dict):
            self.remember_place(nested.get("latitude"), nested.get("longitude"))
            self._remember_scope(nested)

    def _remember_scope(self, payload: dict[str, Any]) -> None:
        found = self.known.get(str(payload.get("id") or ""))
        if found is None or found.raw.get("demo"):
            return
        changed = False
        model = payload.get("model")
        if isinstance(model, str) and model and found.model != model:
            found.model = model
            changed = True
        if not found.raw.get("linked"):
            found.raw["linked"] = True
            changed = True
        if changed:
            self._save()


def build_app(desk: Desk) -> web.Application:
    app = web.Application(middlewares=[errors])
    app["desk"] = desk
    app.router.add_get("/", lambda _request: web.FileResponse(WEB_ROOT / "index.html"))
    app.router.add_get("/tv", lambda _request: web.FileResponse(WEB_ROOT / "tv.html"))
    app.router.add_static("/static", WEB_ROOT)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/catalog", catalog)
    app.router.add_get("/api/sky/tonight", tonight)
    app.router.add_get("/api/sky/gaia/{tail:.*}", gaia_survey)
    app.router.add_get("/api/sky/stars/{tail:.*}", deep_stars)
    app.router.add_get("/api/sky/pictures/{tail:.*}", sky_pictures)
    app.router.add_get("/api/sky/nebula/{name}", sky_nebula)
    app.router.add_get("/api/place", read_place)
    app.router.add_post("/api/place", save_place)
    app.router.add_get("/api/place/weather", place_weather)
    app.router.add_get("/api/scopes", scopes)
    app.router.add_post("/api/discover", do_discover)
    app.router.add_post("/api/scopes", add_scope)
    app.router.add_post("/api/scopes/{scope_id}/connect", connect)
    app.router.add_post("/api/scopes/{scope_id}/disconnect", disconnect)
    app.router.add_post("/api/scopes/{scope_id}/claim", claim)
    app.router.add_post("/api/scopes/{scope_id}/release", release)
    app.router.add_get("/api/scopes/{scope_id}/state", state)
    app.router.add_post("/api/scopes/{scope_id}/goto", goto)
    app.router.add_post("/api/scopes/{scope_id}/move", move)
    app.router.add_post("/api/scopes/{scope_id}/track", track)
    app.router.add_post("/api/scopes/{scope_id}/stop", stop)
    app.router.add_post("/api/scopes/{scope_id}/park", park)
    app.router.add_post("/api/scopes/{scope_id}/power", power)
    app.router.add_post("/api/scopes/{scope_id}/horizon", horizon)
    app.router.add_post("/api/scopes/{scope_id}/mount", mount)
    app.router.add_post("/api/scopes/{scope_id}/focus", focus)
    app.router.add_post("/api/scopes/{scope_id}/filter", filter_wheel)
    app.router.add_post("/api/scopes/{scope_id}/stack", stack)
    app.router.add_post("/api/scopes/{scope_id}/align", align)
    app.router.add_post("/api/scopes/{scope_id}/live", live)
    app.router.add_post("/api/scopes/{scope_id}/video", video)
    app.router.add_post("/api/scopes/{scope_id}/plan", plan)
    app.router.add_post("/api/scopes/{scope_id}/wifi", wifi)
    app.router.add_post("/api/scopes/{scope_id}/files/delete", remove_files)
    app.router.add_post("/api/scopes/{scope_id}/settings", settings)
    app.router.add_get("/api/scopes/{scope_id}/albums", albums)
    app.router.add_get("/api/scopes/{scope_id}/files", files)
    app.router.add_post("/api/scopes/{scope_id}/download", download)
    app.router.add_get("/api/scopes/{scope_id}/media", media)
    app.router.add_get("/api/scopes/{scope_id}/frame.jpg", frame)
    app.router.add_get("/api/ws", websocket)
    return app


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def catalog(request: web.Request) -> web.Response:
    return web.json_response({"targets": search(request.query.get("q", ""), limit=80)})


async def read_place(request: web.Request) -> web.Response:
    return web.json_response(request.app["desk"].place or {})


async def save_place(request: web.Request) -> web.Response:
    body = await _body(request)
    place = request.app["desk"].remember_place(body.get("lat", body.get("latitude")), body.get("lon", body.get("longitude")))
    if place is None:
        raise web.HTTPBadRequest(text="Enter a latitude and longitude.")
    return web.json_response(place)


async def place_weather(request: web.Request) -> web.Response:
    place = request.app["desk"].place
    if not place:
        raise web.HTTPBadRequest(text="Save a place first.")
    latitude = float(place["latitude"])
    longitude = float(place["longitude"])
    weather = await asyncio.to_thread(forecast_at, latitude, longitude)
    sun = sun_altitude(latitude, longitude, datetime.now(timezone.utc))
    return web.json_response({
        "latitude": latitude,
        "longitude": longitude,
        "weather": weather,
        "sky": {"darkness": darkness(sun), "sun_altitude": round(sun, 1), "note": ""},
    })


async def tonight(request: web.Request) -> web.Response:
    try:
        latitude = float(request.query.get("lat", ""))
        longitude = float(request.query.get("lon", ""))
    except ValueError as exc:
        raise web.HTTPBadRequest(text="A latitude and longitude are required.") from exc
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise web.HTTPBadRequest(text="That place is outside the map.")
    return web.json_response(best_tonight(latitude, longitude))


_GAIA_TILE = re.compile(r"^(?:properties|Norder\d+/Dir\d+/Npix\d+\.eph|Norder\d+/Allsky\.eph)$")
_GAIA_ROOT = "https://data.stellarium.org/surveys/gaia"


def _gaia_body(path: str) -> tuple[int, bytes, str]:
    request = urllib.request.Request(f"{_GAIA_ROOT}/{path}", headers={"User-Agent": "SeestarDesk"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            kind = response.headers.get_content_type()
            status = response.status
    except urllib.error.HTTPError as exc:
        return exc.code, b"", "application/octet-stream"
    if path == "properties" and status == 200 and not re.search(r"(?m)^type\s*=", body.decode("utf-8", "replace")):
        body = body.rstrip() + b"\ntype                 = stars\n"
        kind = "text/plain"
    return status, body, kind


async def gaia_survey(request: web.Request) -> web.Response:
    path = request.match_info["tail"].strip("/")
    if not _GAIA_TILE.match(path):
        raise web.HTTPNotFound()
    status, body, kind = await asyncio.to_thread(_gaia_body, path)
    if status == 404:
        raise web.HTTPNotFound()
    if status != 200:
        raise web.HTTPBadGateway(text="The Gaia chart did not answer.")
    return web.Response(body=body, content_type=kind or "application/octet-stream")


_PICTURE_TILE = re.compile(r"^(?:properties|Norder[3-9]/Dir\d+/Npix\d+\.webp|Norder[3-9]/Allsky\.webp)$")
_PICTURE_ROOT = "https://data.stellarium.org/surveys/dss"


def _picture_cache(path: str) -> Path:
    return Path.home() / ".seestar-desk" / "pictures" / path


def _picture_body(path: str) -> tuple[int, bytes, str]:
    kind = "text/plain" if path == "properties" else "image/webp"
    cached = _picture_cache(path)
    if cached.is_file():
        return 200, cached.read_bytes(), kind
    request = urllib.request.Request(f"{_PICTURE_ROOT}/{path}", headers={"User-Agent": "SeestarDesk"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        status = 404 if exc.code in {403, 404} else exc.code
        return status, b"", kind
    if status != 200:
        return status, b"", kind
    if path == "properties" and b"hips_order_min" not in body:
        body = body.rstrip() + b"\nhips_order_min       = 3\n"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(body)
    return 200, body, kind


async def sky_pictures(request: web.Request) -> web.Response:
    path = request.match_info["tail"].strip("/")
    if not _PICTURE_TILE.match(path):
        raise web.HTTPNotFound()
    status, body, kind = await asyncio.to_thread(_picture_body, path)
    if status == 404:
        raise web.HTTPNotFound()
    if status != 200:
        raise web.HTTPBadGateway(text="The sky pictures did not answer.")
    return web.Response(body=body, content_type=kind or "application/octet-stream")


_NEBULA_ROOT = "https://raw.githubusercontent.com/Stellarium/stellarium/master/nebulae/default"
_NEBULA_FILE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,40}\.png$")


def _nebula_names() -> set[str]:
    catalog = WEB_ROOT / "sky" / "data" / "nebulae.json"
    items = json.loads(catalog.read_text())
    return {str(item["file"]) for item in items}


def _nebula_cache(name: str) -> Path:
    return Path.home() / ".seestar-desk" / "nebulae" / name


def _cut_sky(body: bytes) -> bytes:
    """Keep the nebula and drop the black plate so the chart stays dark."""
    import io

    from PIL import Image, ImageChops

    image = Image.open(io.BytesIO(body)).convert("RGBA")
    red, green, blue, _alpha = image.split()
    bright = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    image.putalpha(bright.point(lambda value: 0 if value < 10 else min(255, int((value - 10) * 1.8))))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _nebula_body(name: str) -> tuple[int, bytes]:
    cached = _nebula_cache(name)
    if cached.is_file():
        return 200, cached.read_bytes()
    request = urllib.request.Request(f"{_NEBULA_ROOT}/{name}", headers={"User-Agent": "SeestarDesk"})
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        return (404 if exc.code in {403, 404} else exc.code), b""
    if status != 200:
        return status, b""
    body = _cut_sky(body)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(body)
    return 200, body


async def sky_nebula(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if not _NEBULA_FILE.match(name) or name not in _nebula_names():
        raise web.HTTPNotFound()
    status, body = await asyncio.to_thread(_nebula_body, name)
    if status == 404:
        raise web.HTTPNotFound()
    if status != 200:
        raise web.HTTPBadGateway(text="The nebula picture did not answer.")
    return web.Response(body=body, content_type="image/png")


_DEEP_TILE = re.compile(r"^(?:properties|Norder[0-3]/Dir\d+/Npix\d+\.eph|Norder[0-3]/Allsky\.eph)$")
_DEEP_ROOT = (
    "https://stellarium.sfo2.cdn.digitaloceanspaces.com/"
    "stellarium-web/libswe-1.17.0-48d7978eca/asset_pack_extended/stars"
)


def _deep_cache(path: str) -> Path:
    return Path.home() / ".seestar-desk" / "stars" / path


def _deep_body(path: str) -> tuple[int, bytes, str]:
    kind = "text/plain" if path == "properties" else "application/octet-stream"
    cached = _deep_cache(path)
    if cached.is_file():
        return 200, cached.read_bytes(), kind
    request = urllib.request.Request(f"{_DEEP_ROOT}/{path}", headers={"User-Agent": "SeestarDesk"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        status = 404 if exc.code in {403, 404} else exc.code
        return status, b"", kind
    if status != 200:
        return status, b"", kind
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(body)
    return 200, body, kind


async def deep_stars(request: web.Request) -> web.Response:
    path = request.match_info["tail"].strip("/")
    if not _DEEP_TILE.match(path):
        raise web.HTTPNotFound()
    status, body, kind = await asyncio.to_thread(_deep_body, path)
    if status == 404:
        raise web.HTTPNotFound()
    if status != 200:
        raise web.HTTPBadGateway(text="The deeper star chart did not answer.")
    return web.Response(body=body, content_type=kind)


async def scopes(request: web.Request) -> web.Response:
    desk: Desk = request.app["desk"]
    return web.json_response({"scopes": [desk.describe(item) for item in desk.known.values()]})


async def do_discover(request: web.Request) -> web.Response:
    desk: Desk = request.app["desk"]
    return web.json_response({"scopes": await desk.scan()})


async def add_scope(request: web.Request) -> web.Response:
    desk: Desk = request.app["desk"]
    body = await _body(request)
    found = desk.add_host(str(body.get("host") or ""), str(body.get("name") or ""))
    return web.json_response(desk.describe(found))


async def connect(request: web.Request) -> web.Response:
    desk: Desk = request.app["desk"]
    return web.json_response(await desk.connect(request.match_info["scope_id"]))


async def disconnect(request: web.Request) -> web.Response:
    desk: Desk = request.app["desk"]
    session = desk.sessions.get(request.match_info["scope_id"])
    if session:
        await session.disconnect()
    return web.json_response({"ok": True})


async def claim(request: web.Request) -> web.Response:
    session = await _session(request)
    await session.claim()
    return web.json_response(session.snapshot())


async def release(request: web.Request) -> web.Response:
    session = await _session(request)
    await session.release()
    return web.json_response(session.snapshot())


async def state(request: web.Request) -> web.Response:
    session = await _session(request)
    return web.json_response({"state": session.snapshot(), "log": session.log[-80:]})


async def goto(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    mode = str(body.get("mode") or "star")
    ra = body.get("ra")
    dec = body.get("dec")
    await session.goto(
        float(ra) if ra not in (None, "") else None,  # type: ignore[arg-type]
        float(dec) if dec not in (None, "") else None,  # type: ignore[arg-type]
        str(body.get("name") or "Target"),
        mode,
        bool(body.get("lp_filter")),
    )
    return web.json_response(session.snapshot())


async def move(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    await session.move(float(body.get("angle") or 0), int(body.get("speed") or 2000), float(body.get("dur") or 1))
    return web.json_response(session.snapshot())


async def track(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    await session.command("scope_set_track_state", {"track": bool(body.get("on"))})
    session.state["tracking"] = bool(body.get("on"))
    return web.json_response(session.snapshot())


async def stop(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    stage = body.get("stage")
    await session.stop(str(stage) if stage else None)
    return web.json_response(session.snapshot())


async def power(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    return web.json_response(await session.power(str(body.get("action") or "")))


async def park(request: web.Request) -> web.Response:
    session = await _session(request)
    await session.park()
    return web.json_response(session.snapshot())


async def horizon(request: web.Request) -> web.Response:
    session = await _session(request)
    await session.command("scope_move_to_horizon")
    return web.json_response(session.snapshot())


async def mount(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    await session.set_mount_mode(bool(body.get("equatorial")))
    return web.json_response(session.snapshot())


async def focus(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    action = body.get("action")
    if action == "auto":
        await session.focus_auto()
    elif action == "stop":
        await session.focus_stop()
    else:
        position = body.get("position")
        if position is None:
            base = session.state.get("focus")
            if base is None:
                raise ScopeError("The focuser position is not known yet.")
            position = int(base) + int(body.get("delta") or 0)
        await session.focus_move(int(position))
    try:
        await session.command("get_focuser_position", timeout=4)
    except ScopeError:
        pass
    return web.json_response(session.snapshot())


async def filter_wheel(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    await session.filter_position(int(body.get("position")))
    return web.json_response(session.snapshot())


async def stack(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    if body.get("action") == "stop":
        await session.stop("Stack")
    else:
        gain = body.get("gain")
        await session.stack(bool(body.get("restart", True)), int(gain) if gain not in (None, "") else None)
    return web.json_response(session.snapshot())


async def align(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    action = body.get("action") or "start"
    if action == "stop":
        await session.command("stop_polar_align")
    else:
        await session.polar_align(action != "recheck", int(body.get("dec_pos_index") or 3))
    return web.json_response(session.snapshot())


async def live(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    await session.live(str(body.get("camera") or "tele"), bool(body.get("enabled", True)))
    return web.json_response({"ok": True, "seq": session.frame_seq, "live_error": session.state.get("live_error") or ""})


async def video(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    if body.get("action") == "stop":
        if session._video_task and not session._video_task.done():
            session._video_task.cancel()
        await session.stop()
        return web.json_response(session.snapshot())
    minutes = body.get("minutes")
    await session.start_video(str(body.get("mode") or "planet"), None if minutes in (None, "") else float(minutes))
    return web.json_response(session.snapshot())


async def plan(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    action = body.get("action") or "preview"
    if action != "weather" or "items" in body:
        session.night.replace(body)
    if action == "weather":
        await session.night.refresh_weather()
        return web.json_response(session.snapshot())
    if action == "start":
        session.night.start()
    elif action == "stop":
        session.night.stop()
        await session.stop()
    return web.json_response(session.snapshot())


async def wifi(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    if body.get("action") == "join":
        await session.wifi_join(str(body.get("ssid") or ""), str(body.get("password") or ""))
        return web.json_response(session.snapshot())
    return web.json_response({"networks": await session.wifi_scan(), "station": session.state.get("station") or {}})


async def remove_files(request: web.Request) -> web.Response:
    session = await _session(request)
    body = await _body(request)
    names = [str(name) for name in body.get("names") or [] if name]
    removed = await session.delete_images(str(body.get("folder") or ""), names)
    return web.json_response({"removed": removed})


async def settings(request: web.Request) -> web.Response:
    session = await _session(request)
    await session.apply_settings(await _body(request))
    return web.json_response(session.snapshot())


async def albums(request: web.Request) -> web.Response:
    session = await _session(request)
    return web.json_response({"folders": await list_albums(session), "dest": str(request.app["desk"].downloads)})


async def files(request: web.Request) -> web.Response:
    session = await _session(request)
    folder = request.query.get("folder") or ""
    return web.json_response({"files": await list_folder(session, folder)})


async def download(request: web.Request) -> web.Response:
    desk: Desk = request.app["desk"]
    session = await _session(request)
    body = await _body(request)
    names = [str(name) for name in body.get("names") or [] if name]
    if not names:
        raise ScopeError("Choose at least one file.")
    saved = await asyncio.to_thread(download_files, session, str(body.get("folder") or ""), names, desk.downloads)
    return web.json_response({"saved": saved, "dest": str(desk.downloads)})


async def media(request: web.Request) -> web.Response:
    session = await _session(request)
    path = request.query.get("path") or ""
    if path.startswith("/"):
        path = path[1:]
    if not path.startswith("MyWorks/") or ".." in path.split("/"):
        raise ScopeError("That picture path is not on the telescope.")
    url = session.file_url(path)
    try:
        body, content_type = await asyncio.to_thread(_fetch, url)
    except urllib.error.HTTPError as exc:
        raise ScopeError(f"The telescope has no file at that path ({exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise ScopeError(f"Could not reach the telescope's file server: {exc.reason}") from exc
    return web.Response(body=body, content_type=content_type)


async def frame(request: web.Request) -> web.Response:
    session = await _session(request)
    camera = request.query.get("camera") or "tele"
    if camera not in {"wide", "tele", "stack"}:
        camera = "tele"
    source = session.display_frames if request.query.get("profile") == "tv" else session.frames
    jpeg = source.get(camera) or session.frames.get(camera)
    if not jpeg:
        return web.Response(status=404, text="No frame yet")
    return web.Response(body=jpeg, content_type="image/jpeg")


async def websocket(request: web.Request) -> web.WebSocketResponse:
    desk: Desk = request.app["desk"]
    socket = web.WebSocketResponse(heartbeat=20)
    await socket.prepare(request)
    desk.sockets.add(socket)
    await socket.send_str(json.dumps({
        "type": "hello",
        "scopes": [desk.describe(item) for item in desk.known.values()],
        "place": desk.place,
    }))
    try:
        async for _message in socket:
            pass
    finally:
        desk.sockets.discard(socket)
    return socket


async def _session(request: web.Request) -> ScopeSession:
    desk: Desk = request.app["desk"]
    return await desk.session(request.match_info["scope_id"])


async def _body(request: web.Request) -> dict[str, Any]:
    if not request.can_read_body:
        return {}
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return {}
    return body if isinstance(body, dict) else {}


def _fetch(url: str) -> tuple[bytes, str]:
    with urllib.request.urlopen(url, timeout=30) as response:
        content_type = response.headers.get_content_type()
        return response.read(), content_type or "application/octet-stream"


@web.middleware
async def errors(request: web.Request, handler):  # type: ignore[no-untyped-def]
    try:
        return await handler(request)
    except ScopeError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except asyncio.TimeoutError:
        return web.json_response({"error": "The telescope did not answer in time."}, status=504)
    except OSError as exc:
        return web.json_response({"error": f"Could not reach the telescope: {exc}"}, status=502)

