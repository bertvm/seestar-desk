"""A one-telescope night plan: slew, focus, stack or record, then the next target."""

from __future__ import annotations

import asyncio
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from seestar_desk.errors import ScopeError
from seestar_desk.sky import (
    alt_az,
    angular_distance,
    darkness,
    field_of_view,
    frame_fit,
    moon_advice,
    moon_equatorial,
    moon_sketch,
    mosaic_centers,
    skip_reasons,
    sun_altitude,
)

DEFAULT_SAFETY = {
    "min_battery": 20,
    "min_altitude": 25,
    "stop_before_dawn": True,
    "max_cloud": 80,
    "narrowband": False,
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
            kept = {key: safety[key] for key in DEFAULT_SAFETY if key in safety}
            if "narrowband" in kept:
                kept["narrowband"] = bool(kept["narrowband"])
            self.safety.update(kept)

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
                wait = max(0.0, float(item.get("wait") or 0))
                if wait and not self._stop:
                    self.status["step"] = "waiting"
                    self.status["detail"] = f"Waiting {wait:g} min before {item['name']}"
                    await self.session._emit("state", self.session.snapshot())
                    await self._pause(wait * 60.0)
                if self._stop:
                    break
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

    async def _pause(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if self._stop:
                return
            await asyncio.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

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
        fitted = frame_fit(item.get("size_arcmin"), str(self.session.state.get("model") or ""))
        if fitted:
            copy["fit"] = fitted["fit"]
        copy["moon_note"] = self._moon_note(item, moment)
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
        reasons = skip_reasons(
            altitude=self._altitude(item, moment),
            battery=_float(self.session.state.get("battery")),
            sun_alt=sun_alt,
            cloud=cloud,
            safety=self.safety,
        )
        note = self._moon_note(item, moment)
        if note.startswith("The Moon is") and "still suits" not in note:
            reasons.append(note)
        return reasons

    def _moon_note(self, item: dict[str, Any], moment: datetime) -> str:
        lat = _float(self.session.state.get("latitude"))
        lon = _float(self.session.state.get("longitude"))
        ra = _float(item.get("ra"))
        dec = _float(item.get("dec"))
        if None in (lat, lon, ra, dec):
            return ""
        moon_ra, moon_dec = moon_equatorial(moment)
        moon_alt, _moon_az = alt_az(moon_ra, moon_dec, float(lat), float(lon), moment)
        separation = angular_distance(float(ra), float(dec), moon_ra, moon_dec)
        return moon_advice(str(item.get("kind") or ""), separation, moon_alt > 0, bool(self.safety.get("narrowband")))


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
        "kind": str(item.get("kind") or ""),
        "size_arcmin": None if item.get("size_arcmin") in (None, "") else float(item.get("size_arcmin")),
        "autofocus": bool(item.get("autofocus", True)),
        "rows": max(1, int(item.get("rows") or 1)),
        "cols": max(1, int(item.get("cols") or 1)),
        "overlap": float(item.get("overlap") if item.get("overlap") is not None else 20),
        "wait": max(0.0, float(item.get("wait") or 0)),
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


def forecast_at(lat: float, lon: float) -> dict[str, Any]:
    return _open_meteo(lat, lon)


def _open_meteo(lat: float, lon: float) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "current": "temperature_2m,relative_humidity_2m,dew_point_2m,cloud_cover,precipitation,wind_speed_10m,wind_gusts_10m",
            "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m,cloud_cover,precipitation,wind_speed_10m,wind_gusts_10m",
            "forecast_days": "7",
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
    if not isinstance(payload, dict):
        return {"error": "Weather is unavailable."}
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    samples = _hourly_samples(payload)
    summary = summarize_night(samples, lat, lon)
    nights = night_outlooks(samples, lat, lon)
    summary["nights"] = nights
    summary["moon"] = moon_sketch()
    if nights:
        summary["score"] = nights[0]["score"]
        summary["score_label"] = nights[0]["label"]
        summary["advice"] = nights[0]["advice"]
    summary.update(
        {
            "cloud_cover": current.get("cloud_cover"),
            "precipitation": current.get("precipitation"),
            "wind_speed": current.get("wind_speed_10m"),
            "wind_gust": current.get("wind_gusts_10m"),
            "temperature": current.get("temperature_2m"),
            "humidity": current.get("relative_humidity_2m"),
            "dew_point": current.get("dew_point_2m"),
            "time": current.get("time") or "",
            "source": "Open-Meteo",
        }
    )
    return summary


def _hourly_samples(payload: dict[str, Any]) -> list[dict[str, Any]]:
    hourly = payload.get("hourly") if isinstance(payload.get("hourly"), dict) else {}
    stamps = hourly.get("time") if isinstance(hourly.get("time"), list) else []
    offset = int(payload.get("utc_offset_seconds") or 0)
    zone = timezone(timedelta(seconds=offset))
    samples = []
    for index, stamp in enumerate(stamps):
        try:
            local = datetime.fromisoformat(str(stamp)).replace(tzinfo=zone)
        except ValueError:
            continue
        samples.append(
            {
                "time": local.astimezone(timezone.utc),
                "label": local.strftime("%H:%M"),
                "end": (local + timedelta(hours=1)).strftime("%H:%M"),
                "cloud": _at(hourly.get("cloud_cover"), index),
                "temp": _at(hourly.get("temperature_2m"), index),
                "dew": _at(hourly.get("dew_point_2m"), index),
                "humidity": _at(hourly.get("relative_humidity_2m"), index),
                "wind": _at(hourly.get("wind_speed_10m"), index),
                "gust": _at(hourly.get("wind_gusts_10m"), index),
                "precip": _at(hourly.get("precipitation"), index),
                "day": local.strftime("%A"),
                "date": f"{local.strftime('%b')} {local.day}",
            }
        )
    return samples


def _at(values: Any, index: int) -> float | None:
    if not isinstance(values, list) or index >= len(values):
        return None
    return _float(values[index])


def summarize_night(samples: list[dict[str, Any]], lat: float, lon: float, moment: datetime | None = None) -> dict[str, Any]:
    """Cloud, the clearest stretch, and dew risk for the next dark hours."""
    moment = moment or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    upcoming = [sample for sample in samples if sample["time"] >= moment - timedelta(minutes=30)]
    dark = [sample for sample in upcoming if sun_altitude(lat, lon, sample["time"]) <= -12]
    run = _first_run(dark)
    if len(run) < 2:
        run = _first_run([sample for sample in upcoming if sun_altitude(lat, lon, sample["time"]) <= -6])
    if len(run) < 2:
        return {"hours": [], "window": "", "low": None, "dew_risk": "", "note": "The sky does not get dark in the next two days."}
    run = run[:14]
    clearest = _clearest(run)
    temps = [sample["temp"] for sample in run if sample.get("temp") is not None]
    return {
        "hours": [{"label": sample["label"], "cloud": sample.get("cloud")} for sample in run],
        "window": f"{clearest[0]['label']}–{clearest[-1]['end']}",
        "low": None if not temps else round(min(temps), 1),
        "dew_risk": _dew_risk(run),
        "note": "",
    }


def night_outlooks(samples: list[dict[str, Any]], lat: float, lon: float) -> list[dict[str, Any]]:
    """One card per dark stretch in the hourly forecast."""
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for sample in samples:
        dark = sun_altitude(lat, lon, sample["time"]) <= -12
        if not dark:
            if current:
                runs.append(current)
                current = []
            continue
        if current and sample["time"] - current[-1]["time"] > timedelta(hours=1, minutes=5):
            runs.append(current)
            current = []
        current.append(sample)
    if current:
        runs.append(current)
    nights = []
    for run in runs:
        if len(run) < 2:
            continue
        middle = run[len(run) // 2]
        sketch = moon_sketch(middle["time"])
        hours = []
        for sample in run:
            moon_ra, moon_dec = moon_equatorial(sample["time"])
            moon_alt, _moon_az = alt_az(moon_ra, moon_dec, lat, lon, sample["time"])
            up = moon_alt > 0
            hours.append(
                {
                    "label": sample["label"],
                    "end": sample["end"],
                    "cloud": sample.get("cloud"),
                    "humidity": sample.get("humidity"),
                    "wind": sample.get("wind"),
                    "temp": sample.get("temp"),
                    "dew": sample.get("dew"),
                    "precip": sample.get("precip"),
                    "moon_up": up,
                    "score": _hour_score(sample, up, sketch["illumination"]),
                }
            )
        shown = hours[:16]
        score = round(sum(hour["score"] for hour in shown) / len(shown))
        clearest = _clearest_pair(shown)
        up_labels = [hour["label"] for hour in shown if hour["moon_up"]]
        if not up_labels:
            moon_span = "Down"
        elif len(up_labels) == len(shown):
            moon_span = "Up all night"
        else:
            moon_span = f"Up {up_labels[0]}–{up_labels[-1]}"
        clouds = [hour["cloud"] for hour in shown if hour["cloud"] is not None]
        humid = [hour["humidity"] for hour in shown if hour["humidity"] is not None]
        winds = [hour["wind"] for hour in shown if hour["wind"] is not None]
        rain = [hour["precip"] for hour in shown if hour["precip"] is not None]
        nights.append(
            {
                "day": middle.get("day") or "",
                "date": middle.get("date") or "",
                "score": score,
                "label": score_words(score),
                "advice": score_advice(score, sketch["illumination"]),
                "dark_hours": round(len(run), 1),
                "starts": shown[0]["label"],
                "ends": shown[-1]["end"],
                "window": "" if clearest is None else f"{clearest[0]['label']}–{clearest[1]['end']}",
                "clouds": None if not clouds else round(sum(clouds) / len(clouds)),
                "humidity": None if not humid else round(sum(humid) / len(humid)),
                "wind": None if not winds else round(sum(winds) / len(winds), 1),
                "precip": None if not rain else round(sum(rain), 1),
                "spark": [None if hour["cloud"] is None else round(hour["cloud"]) for hour in shown],
                "hours": shown,
                "moon": {**sketch, "span": moon_span},
            }
        )
    return nights[:7]


def score_words(score: float) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Great"
    if score >= 55:
        return "Good"
    if score >= 40:
        return "Fair"
    if score >= 25:
        return "Poor"
    return "Bad"


def score_advice(score: float, illumination: float) -> str:
    if score >= 70 and illumination < 0.4:
        return "Clear enough for faint nebulae and galaxies."
    if score >= 55:
        return "Decent conditions. Brighter clusters and galaxies will show well."
    if illumination >= 0.6:
        return "A bright Moon. Planets, the Moon, and star clusters hold up better than faint nebulae."
    return "Cloud or haze is in the way. Wait for a clearer stretch."


def _hour_score(sample: dict[str, Any], moon_up: bool, illumination: float) -> int:
    cloud = float(sample.get("cloud") or 0)
    score = 100.0 - cloud * 0.62
    if moon_up:
        score -= float(illumination) * 22.0
    temp = sample.get("temp")
    dew = sample.get("dew")
    if temp is not None and dew is not None and float(temp) - float(dew) < 2:
        score -= 12
    precip = sample.get("precip")
    if precip is not None and float(precip) > 0.1:
        score -= 18
    return int(max(0, min(100, round(score))))


def _clearest_pair(hours: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if len(hours) < 2:
        return None
    best_index = 0
    best_cloud = 10_000.0
    for index in range(len(hours) - 1):
        pair = (hours[index].get("cloud") or 0) + (hours[index + 1].get("cloud") or 0)
        if pair < best_cloud:
            best_cloud = pair
            best_index = index
    return hours[best_index], hours[best_index + 1]


def _first_run(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not samples:
        return []
    run = [samples[0]]
    for sample in samples[1:]:
        if sample["time"] - run[-1]["time"] > timedelta(hours=1, minutes=5):
            break
        run.append(sample)
    return run


def _clearest(hours: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_key: tuple[Any, ...] | None = None
    best = hours
    for start in range(len(hours)):
        for end in range(start, len(hours)):
            segment = hours[start : end + 1]
            clouds = [sample["cloud"] for sample in segment if sample.get("cloud") is not None]
            if not clouds:
                continue
            average = sum(clouds) / len(clouds)
            clear = average <= 40
            key = (1 if clear else 0, len(segment) if clear else 0, -average, len(segment))
            if best_key is None or key > best_key:
                best_key = key
                best = segment
    return best


def _dew_risk(hours: list[dict[str, Any]]) -> str:
    gaps = [
        float(sample["temp"]) - float(sample["dew"])
        for sample in hours
        if sample.get("temp") is not None and sample.get("dew") is not None
    ]
    if not gaps:
        return ""
    gap = min(gaps)
    if gap <= 1.5:
        return "High"
    if gap <= 3.5:
        return "Moderate"
    return "Low"
