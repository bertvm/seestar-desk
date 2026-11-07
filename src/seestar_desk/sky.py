"""Altitudes, twilight, mosaic panels, and the reasons to skip a target."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def julian_date(moment: datetime) -> float:
    return _aware(moment).timestamp() / 86400.0 + 2440587.5


def gmst_hours(moment: datetime) -> float:
    days = julian_date(moment) - 2451545.0
    return (18.697374558 + 24.06570982441908 * days) % 24


def lst_hours(moment: datetime, lon_deg: float) -> float:
    return (gmst_hours(moment) + lon_deg / 15.0) % 24


def alt_az(ra_hours: float, dec_deg: float, lat_deg: float, lon_deg: float, moment: datetime) -> tuple[float, float]:
    """Return altitude and azimuth in degrees for an equatorial target."""
    hour_angle = math.radians((lst_hours(moment, lon_deg) - ra_hours) * 15.0)
    dec = math.radians(dec_deg)
    lat = math.radians(lat_deg)
    sin_alt = math.sin(dec) * math.sin(lat) + math.cos(dec) * math.cos(lat) * math.cos(hour_angle)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    altitude = math.degrees(math.asin(sin_alt))
    cos_alt = math.cos(math.radians(altitude)) or 1e-6
    cos_az = (math.sin(dec) - math.sin(lat) * sin_alt) / (math.cos(lat) * cos_alt)
    cos_az = max(-1.0, min(1.0, cos_az))
    azimuth = math.degrees(math.acos(cos_az))
    if math.sin(hour_angle) > 0:
        azimuth = 360.0 - azimuth
    return altitude, azimuth


def sun_equatorial(moment: datetime) -> tuple[float, float]:
    """Low-precision solar right ascension (hours) and declination (degrees)."""
    days = julian_date(moment) - 2451545.0
    mean_long = math.radians((280.460 + 0.9856474 * days) % 360.0)
    anomaly = math.radians((357.528 + 0.9856003 * days) % 360.0)
    ecliptic = mean_long + math.radians(1.915 * math.sin(anomaly) + 0.020 * math.sin(2 * anomaly))
    tilt = math.radians(23.439 - 0.0000004 * days)
    ra = math.degrees(math.atan2(math.cos(tilt) * math.sin(ecliptic), math.cos(ecliptic)))
    ra_hours = (ra / 15.0) % 24.0
    declination = math.degrees(math.asin(math.sin(tilt) * math.sin(ecliptic)))
    return ra_hours, declination


def sun_altitude(lat_deg: float, lon_deg: float, moment: datetime) -> float:
    ra_hours, declination = sun_equatorial(moment)
    altitude, _azimuth = alt_az(ra_hours, declination, lat_deg, lon_deg, moment)
    return altitude


def darkness(sun_alt: float) -> str:
    if sun_alt >= 0:
        return "daylight"
    if sun_alt >= -6:
        return "civil twilight"
    if sun_alt >= -12:
        return "nautical twilight"
    if sun_alt >= -18:
        return "astronomical twilight"
    return "dark"


def field_of_view(model: str) -> tuple[float, float]:
    """Approximate telephoto field, width then height, in degrees."""
    text = (model or "").lower()
    if "s30" in text:
        return 2.4, 1.35
    return 1.29, 0.72


def mosaic_centers(
    ra_hours: float,
    dec_deg: float,
    rows: int,
    cols: int,
    overlap_percent: float,
    fov_ra_deg: float,
    fov_dec_deg: float,
) -> list[dict[str, float | int]]:
    """Panel centers around one target. Overlap is a percent of the field."""
    rows = max(1, int(rows))
    cols = max(1, int(cols))
    overlap = min(0.8, max(0.0, float(overlap_percent) / 100.0))
    step_dec = fov_dec_deg * (1.0 - overlap)
    step_ra_deg = fov_ra_deg * (1.0 - overlap)
    cos_dec = math.cos(math.radians(dec_deg)) or 1e-3
    centers: list[dict[str, float | int]] = []
    for row in range(rows):
        for col in range(cols):
            delta_dec = (row - (rows - 1) / 2.0) * step_dec
            delta_ra = ((col - (cols - 1) / 2.0) * step_ra_deg) / (15.0 * cos_dec)
            centers.append(
                {
                    "ra": (ra_hours + delta_ra) % 24.0,
                    "dec": max(-90.0, min(90.0, dec_deg + delta_dec)),
                    "row": row,
                    "col": col,
                }
            )
    return centers


def moon_equatorial(moment: datetime) -> tuple[float, float]:
    """Approximate lunar right ascension (hours) and declination (degrees)."""
    days = julian_date(moment) - 2451545.0
    mean_long = math.radians((218.316 + 13.176396 * days) % 360.0)
    anomaly = math.radians((134.963 + 13.064993 * days) % 360.0)
    node = math.radians((93.272 + 13.229350 * days) % 360.0)
    longitude = mean_long + math.radians(6.289 * math.sin(anomaly))
    latitude = math.radians(5.128 * math.sin(node))
    tilt = math.radians(23.439)
    ra = math.atan2(
        math.sin(longitude) * math.cos(tilt) - math.tan(latitude) * math.sin(tilt),
        math.cos(longitude),
    )
    declination = math.asin(
        math.sin(latitude) * math.cos(tilt) + math.cos(latitude) * math.sin(tilt) * math.sin(longitude)
    )
    return (math.degrees(ra) / 15.0) % 24.0, math.degrees(declination)


def angular_distance(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Separation in degrees between two equatorial positions. RA is in hours."""
    first_ra = math.radians(ra1 * 15.0)
    second_ra = math.radians(ra2 * 15.0)
    first_dec = math.radians(dec1)
    second_dec = math.radians(dec2)
    cosine = math.sin(first_dec) * math.sin(second_dec) + math.cos(first_dec) * math.cos(second_dec) * math.cos(first_ra - second_ra)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _dark_window(lat_deg: float, lon_deg: float, moment: datetime) -> tuple[datetime, datetime, str] | None:
    """The next stretch when the sun is low enough to image, within 20 hours."""
    step = timedelta(minutes=10)
    samples = [moment + step * index for index in range(20 * 6 + 1)]
    suns = [sun_altitude(lat_deg, lon_deg, sample) for sample in samples]
    for limit, label in ((-18.0, "astronomical night"), (-12.0, "nautical twilight")):
        dark = [index for index, altitude in enumerate(suns) if altitude <= limit]
        if not dark:
            continue
        start = dark[0]
        end = start
        for index in dark[1:]:
            if index > end + 1:
                break
            end = index
        if end == start:
            continue
        return samples[start], samples[end], label
    return None


def best_tonight(lat_deg: float, lon_deg: float, moment: datetime | None = None, limit: int = 5) -> dict[str, Any]:
    """Rank catalog objects by how high and how long they stay up in the coming dark hours."""
    from seestar_desk.catalog import targets

    moment = _aware(moment or datetime.now(timezone.utc))
    window = _dark_window(lat_deg, lon_deg, moment)
    place = {"latitude": round(lat_deg, 4), "longitude": round(lon_deg, 4)}
    if window is None:
        return {"place": place, "window": None, "picks": [], "note": "The sky does not get dark in the next 20 hours."}
    start, end, label = window
    steps = max(1, int((end - start).total_seconds() / 1200))
    times = [start + (end - start) * (index / steps) for index in range(steps + 1)]
    step_minutes = (times[1] - times[0]).total_seconds() / 60.0
    moon_places = []
    for sample in times:
        moon_ra, moon_dec = moon_equatorial(sample)
        moon_alt, moon_az = alt_az(moon_ra, moon_dec, lat_deg, lon_deg, sample)
        moon_places.append((moon_ra, moon_dec, moon_alt, moon_az))
    ranked: list[dict[str, Any]] = []
    for target in targets():
        altitudes = [alt_az(float(target["ra"]), float(target["dec"]), lat_deg, lon_deg, sample)[0] for sample in times]
        peak = max(altitudes)
        if peak < 35:
            continue
        peak_index = altitudes.index(peak)
        minutes_high = sum(step_minutes for altitude in altitudes if altitude >= 40)
        magnitude = float(target["mag"]) if target["mag"] is not None else 9.0
        moon_ra, moon_dec, moon_alt, _moon_az = moon_places[peak_index]
        separation = angular_distance(float(target["ra"]), float(target["dec"]), moon_ra, moon_dec)
        moon_penalty = (40.0 - separation) / 10.0 if moon_alt > 0 and separation < 40 else 0.0
        score = (minutes_high / 60.0) * 3.0 + peak / 15.0 + max(0.0, 8.0 - magnitude) * 0.5 - moon_penalty
        ranked.append(
            {
                "id": target["id"],
                "name": target["name"],
                "kind": target["kind"],
                "ra": target["ra"],
                "dec": target["dec"],
                "mag": target["mag"],
                "peak_altitude": round(peak, 1),
                "peak_at": times[peak_index].isoformat().replace("+00:00", "Z"),
                "minutes_above_40": int(round(minutes_high)),
                "moon_distance": round(separation, 0),
                "moon_up": moon_alt > 0,
                "score": round(score, 2),
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return {
        "place": place,
        "window": {"start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z"), "darkness": label},
        "picks": ranked[:limit],
        "note": "",
    }


def skip_reasons(
    *,
    altitude: float | None,
    battery: float | None,
    sun_alt: float | None,
    cloud: float | None,
    safety: dict[str, Any],
) -> list[str]:
    """Conditions that should stop this exposure. Missing data is left unchecked."""
    reasons: list[str] = []
    min_alt = float(safety.get("min_altitude", 25))
    if altitude is not None and altitude < min_alt:
        reasons.append(f"Altitude is {altitude:.0f}°, below the {min_alt:.0f}° limit")
    min_battery = float(safety.get("min_battery", 20))
    if battery is not None and battery < min_battery:
        reasons.append(f"Battery is {battery:.0f}%, below the {min_battery:.0f}% limit")
    if safety.get("stop_before_dawn", True) and sun_alt is not None and sun_alt > -18:
        reasons.append(f"The sky is in {darkness(sun_alt)}")
    max_cloud = float(safety.get("max_cloud", 80))
    if cloud is not None and cloud >= max_cloud:
        reasons.append(f"Cloud cover is {cloud:.0f}%")
    return reasons
