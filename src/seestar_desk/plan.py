"""A one-telescope night plan: slew, focus, stack or record, then the next target."""

from __future__ import annotations

import asyncio
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from seestar_desk.errors import ScopeError
from seestar_desk.sky import alt_az, darkness, field_of_view, mosaic_centers, skip_reasons, sun_altitude

DEFAULT_SAFETY = {
    "min_battery": 20,
    "min_altitude": 25,
    "stop_before_dawn": True,
    "max_cloud": 80,
}


class NightPlan:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.items: list[dict[str, Any]] = []
        self.options = {"park_when_finished": False}
        self.safety = dict(DEFAULT_SAFETY)
        self.weather: dict[str, Any] | None = None
        self.status: dict[str, Any] = {
            "running": False,
            "index": -1,
            "step": "idle",
            "detail": "",
            "skipped": [],
        }
        self._task: asyncio.Task | None = None
        self._stop = False

    def replace(self, payload: dict[str, Any]) -> None:
        items = payload.get("items")
        if isinstance(items, list):
            self.items = [_clean_item(item, self.session.state.get("model")) for item in items if isinstance(item, dict)]
        options = payload.get("options")
        if isinstance(options, dict):
            self.options["park_when_finished"] = bool(options.get("park_when_finished"))
        safety = payload.get("safety")
        if isinstance(safety, dict):
            self.safety.update({key: safety[key] for key in DEFAULT_SAFETY if key in safety})

    def public(self) -> dict[str, Any]:
        moment = datetime.now(timezone.utc)
        return {
            "items": [self._annotate(item, moment) for item in self.items],
            "options": dict(self.options),
            "safety": dict(self.safety),
            "weather": self.weather,
            "status": dict(self.status),
            "sky": self.sky(moment),
        }

    def sky(self, moment: datetime | None = None) -> dict[str, Any]:
        moment = moment or datetime.now(timezone.utc)
        lat = _float(self.session.state.get("latitude"))
        lon = _float(self.session.state.get("longitude"))
        if lat is None or lon is None:
            return {"darkness": "", "sun_altitude": None, "note": "Set the site location to judge altitude and darkness."}
        sun_alt = sun_altitude(lat, lon, moment)
        return {"darkness": darkness(sun_alt), "sun_altitude": round(sun_alt, 1), "note": ""}

    async def refresh_weather(self) -> dict[str, Any] | None:
        lat = _float(self.session.state.get("latitude"))
        lon = _float(self.session.state.get("longitude"))
        if lat is None or lon is None:
            self.weather = None
            return None
        self.weather = await asyncio.to_thread(_open_meteo, lat, lon)
        return self.weather

    def start(self) -> None:
        if self._task and not self._task.done():
            raise ScopeError("A plan is already running.")
        if not self.items:
            raise ScopeError("Add at least one target.")
        self._stop = False
        self.status = {"running": True, "index": 0, "step": "starting", "detail": "", "skipped": []}
        self._task = asyncio.create_task(self._run(), name="night-plan")

    def stop(self) -> None:
        self._stop = True
        self.status["step"] = "stopping"
        self.status["detail"] = "Stopping after the current step."

    async def guard_exposure(self) -> None:
        """Stop a stack the user started by hand when a safety limit is crossed."""
        if self.status.get("running"):
            return
        phase = str((self.session.state.get("stack") or {}).get("state") or "")
        if phase not in {"start", "working", "stacking"}:
            return
        reasons = self._hard_reasons(self._current_target(), datetime.now(timezone.utc))
        if not reasons:
            return
        self.session.state["stack"]["state"] = "stopped"
        await self.session.stop("Stack")
        await self.session._note("Stopped the stack: " + "; ".join(reasons))

    async def _run(self) -> None:
        try:
            await self.session.claim()
            for index, item in enumerate(self.items):
                if self._stop:
                    break
                self.status["index"] = index
                moment = datetime.now(timezone.utc)
                reasons = self._hard_reasons(item, moment)
                if reasons:
                    self.status["skipped"].append({"name": item["name"], "reasons": reasons})
                    self.status["detail"] = f"Skipped {item['name']}: {reasons[0]}"
                    await self.session._note(self.status["detail"])
                    continue
                if item["mode"] in {"planet", "moon", "scenery"}:
                    await self._record(item)
                else:
                    await self._image_target(item)
            if self._stop:
                self.status["step"] = "stopped"
                await self.session.stop()
            else:
                self.status["step"] = "complete"
                self.status["detail"] = "The list is finished."
                if self.options.get("park_when_finished"):
                    self.status["detail"] = "The list is finished. Parking."
                    await self.session.park()
        except asyncio.CancelledError:
            self.status["step"] = "stopped"
            raise
        except ScopeError as exc:
            self.status["step"] = "failed"
            self.status["detail"] = str(exc)
            self.session.state["last_error"] = str(exc)
        finally:
            self.status["running"] = False
            await self.session._emit("state", self.session.snapshot())

    async def _image_target(self, item: dict[str, Any]) -> None:
        panels = mosaic_centers(
            float(item["ra"]),
            float(item["dec"]),
            int(item["rows"]),
            int(item["cols"]),
            float(item["overlap"]),
            float(item["fov_ra"]),
            float(item["fov_dec"]),
        )
        for panel in panels:
            if self._stop:
                return
            label = item["name"] if len(panels) == 1 else f"{item['name']} {int(panel['row']) + 1}×{int(panel['col']) + 1}"
            self.status["step"] = "slew"
            self.status["detail"] = f"Going to {label}"
            await self.session.goto(float(panel["ra"]), float(panel["dec"]), label, "star", bool(item.get("lp_filter")))
            await self._wait_view()
            if item.get("autofocus"):
                self.status["step"] = "focus"
                self.status["detail"] = f"Focusing {label}"
                self.session.state["focus_state"] = "working"
                await self.session.focus_auto()
                await self._wait_until(lambda: self.session.state.get("focus_state") in {"complete", "fail", "cancel"})
            if item.get("filter") is not None:
                await self.session.filter_position(int(item["filter"]))
            self.status["step"] = "stack"
            self.status["detail"] = f"Stacking {label} for {item['minutes']:g} min"
            await self.session.stack(True, int(item["gain"]) if item.get("gain") is not None else None)
            await self._expose(float(item["minutes"]) * 60.0, item)
            await self.session.stop("Stack")

    async def _record(self, item: dict[str, Any]) -> None:
        self.status["step"] = "video"
        self.status["detail"] = f"Recording {item['name']} for {item['minutes']:g} min"
        await self.session.start_video(item["mode"], None)
        await self._expose(float(item["minutes"]) * 60.0, item)
        await self.session.stop()

    async def _expose(self, seconds: float, item: dict[str, Any]) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if self._stop:
                return
            reasons = self._hard_reasons(item, datetime.now(timezone.utc))
            if reasons:
                self.status["detail"] = "Stopped early: " + reasons[0]
                await self.session._note(self.status["detail"])
                return
            await asyncio.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    async def _wait_view(self) -> None:
        await self._wait_until(
            lambda: str((self.session.state.get("view") or {}).get("state") or "") in {"complete", "fail", "cancel"}
        )

    async def _wait_until(self, ready, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ready():
                return
            await asyncio.sleep(0.05)
        raise ScopeError("The telescope did not finish that step in time.")

    def _annotate(self, item: dict[str, Any], moment: datetime) -> dict[str, Any]:
        copy = dict(item)
        altitude = self._altitude(item, moment)
        copy["altitude"] = None if altitude is None else round(altitude, 1)
        copy["reasons"] = self._hard_reasons(item, moment)
        return copy

    def _current_target(self) -> dict[str, Any]:
        ra = _float(self.session.state.get("ra"))
        dec = _float(self.session.state.get("dec"))
        return {"ra": ra if ra is not None else 0.0, "dec": dec if dec is not None else 0.0, "name": self.session.state.get("target") or "Current target"}

    def _altitude(self, item: dict[str, Any], moment: datetime) -> float | None:
        lat = _float(self.session.state.get("latitude"))
        lon = _float(self.session.state.get("longitude"))
        ra = _float(item.get("ra"))
        dec = _float(item.get("dec"))
        if None in (lat, lon, ra, dec):
            return None
        altitude, _azimuth = alt_az(float(ra), float(dec), float(lat), float(lon), moment)
        return altitude

    def _hard_reasons(self, item: dict[str, Any], moment: datetime) -> list[str]:
        lat = _float(self.session.state.get("latitude"))
        lon = _float(self.session.state.get("longitude"))
        sun_alt = sun_altitude(lat, lon, moment) if lat is not None and lon is not None else None
        cloud = None
        if isinstance(self.weather, dict):
            cloud = _float(self.weather.get("cloud_cover"))
        return skip_reasons(
            altitude=self._altitude(item, moment),
            battery=_float(self.session.state.get("battery")),
            sun_alt=sun_alt,
            cloud=cloud,
            safety=self.safety,
        )


def _clean_item(item: dict[str, Any], model: Any) -> dict[str, Any]:
    fov_ra, fov_dec = field_of_view(str(model or ""))
    mode = str(item.get("mode") or "star")
    if mode not in {"star", "planet", "moon", "scenery"}:
        mode = "star"
    gain = item.get("gain")
    return {
        "name": str(item.get("name") or "Target").strip() or "Target",
        "ra": float(item.get("ra") or 0),
        "dec": float(item.get("dec") or 0),
        "mode": mode,
        "minutes": max(0.0, float(item.get("minutes") or 0)),
        "gain": None if gain in (None, "") else int(gain),
        "filter": None if item.get("filter") in (None, "") else int(item["filter"]),
        "lp_filter": bool(item.get("lp_filter")),
        "autofocus": bool(item.get("autofocus", True)),
        "rows": max(1, int(item.get("rows") or 1)),
        "cols": max(1, int(item.get("cols") or 1)),
        "overlap": float(item.get("overlap") if item.get("overlap") is not None else 20),
        "fov_ra": float(item.get("fov_ra") or fov_ra),
        "fov_dec": float(item.get("fov_dec") or fov_dec),
    }


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _open_meteo(lat: float, lon: float) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "current": "cloud_cover,precipitation,wind_speed_10m",
            "timezone": "auto",
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{query}"
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            import json

            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return {"error": f"Weather is unavailable: {exc}"}
    current = payload.get("current") if isinstance(payload, dict) else {}
    if not isinstance(current, dict):
        return {"error": "Weather is unavailable."}
    return {
        "cloud_cover": current.get("cloud_cover"),
        "precipitation": current.get("precipitation"),
        "wind_speed": current.get("wind_speed_10m"),
        "time": current.get("time") or "",
    }
