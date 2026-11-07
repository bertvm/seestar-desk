"""HTTP and WebSocket server for the desk."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from aiohttp import web

from seestar_desk.album import download_files, list_albums, list_folder
from seestar_desk.catalog import search
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
        self.path = Path.home() / ".seestar-desk" / "scopes.json"
        self.downloads = Path.home() / "Pictures" / "Seestar Desk"
        self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in saved.get("scopes") or []:
            found = FoundScope(
                host=item["host"],
                name=item.get("name") or "",
                model=item.get("model") or "",
                ssid=item.get("ssid") or "",
            )
            self.known[found.id] = found

    def _save(self) -> None:
        scopes = [
            {"host": item.host, "name": item.name, "model": item.model, "ssid": item.ssid}
            for item in self.known.values()
            if not item.raw.get("demo") and item.control_port == 4700
        ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"scopes": scopes}, indent=2), encoding="utf-8")

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
        message = {"type": kind}
        message.update(payload)
        await self.broadcast(message)


def build_app(desk: Desk) -> web.Application:
    app = web.Application(middlewares=[errors])
    app["desk"] = desk
    app.router.add_get("/", lambda _request: web.FileResponse(WEB_ROOT / "index.html"))
    app.router.add_static("/static", WEB_ROOT)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/catalog", catalog)
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
    await session.command("scope_park")
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
    return web.json_response({"ok": True, "seq": session.frame_seq})


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
    camera = "wide" if request.query.get("camera") == "wide" else "tele"
    jpeg = session.frames.get(camera)
    if not jpeg:
        return web.Response(status=404, text="No frame yet")
    return web.Response(body=jpeg, content_type="image/jpeg")


async def websocket(request: web.Request) -> web.WebSocketResponse:
    desk: Desk = request.app["desk"]
    socket = web.WebSocketResponse(heartbeat=20)
    await socket.prepare(request)
    desk.sockets.add(socket)
    await socket.send_str(json.dumps({"type": "hello", "scopes": [desk.describe(item) for item in desk.known.values()]}))
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

