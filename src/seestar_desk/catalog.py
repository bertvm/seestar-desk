"""Bright-object list for GoTo. Coordinates are J2000, right ascension in hours."""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

TYPE_LABELS = {
    "OC": "open cluster",
    "GC": "globular cluster",
    "SG": "galaxy",
    "IG": "galaxy",
    "EG": "galaxy",
    "S": "galaxy",
    "DN": "nebula",
    "PN": "planetary nebula",
    "dbl": "double star",
    "ast": "asterism",
}

NAME_OVERRIDES = {
    33: "Triangulum Galaxy",
    76: "Little Dumbbell",
    82: "Cigar Galaxy",
    101: "Pinwheel Galaxy",
    102: "Spindle Galaxy",
}

# Major axis in arcminutes, used to compare an object with the telescope's frame.
SIZE_ARCMIN = {
    "M1": 6, "M8": 90, "M13": 20, "M16": 35, "M17": 40, "M20": 28, "M27": 8,
    "M31": 178, "M32": 9, "M33": 71, "M42": 85, "M43": 20, "M44": 95, "M45": 110,
    "M51": 11, "M57": 1.4, "M64": 10, "M76": 3, "M78": 8, "M81": 27, "M82": 11,
    "M97": 3, "M101": 29, "M104": 9,
    "NGC 869": 30, "NGC 884": 30, "NGC 7000": 120, "NGC 6992": 60, "NGC 7293": 16,
    "NGC 2244": 80, "IC 1805": 150, "IC 1848": 150, "NGC 1499": 145,
}


EXTRAS = [
    {"id": "NGC 869", "name": "Double Cluster, west", "ra": 2.316, "dec": 57.133, "kind": "open cluster", "mag": 3.7},
    {"id": "NGC 884", "name": "Double Cluster, east", "ra": 2.372, "dec": 57.133, "kind": "open cluster", "mag": 3.8},
    {"id": "NGC 7000", "name": "North America Nebula", "ra": 20.980, "dec": 44.330, "kind": "nebula", "mag": 4.0},
    {"id": "NGC 6992", "name": "Veil Nebula", "ra": 20.944, "dec": 31.717, "kind": "nebula", "mag": 7.0},
    {"id": "NGC 7293", "name": "Helix Nebula", "ra": 22.493, "dec": -20.837, "kind": "planetary nebula", "mag": 7.6},
    {"id": "NGC 2244", "name": "Rosette Nebula", "ra": 6.540, "dec": 4.950, "kind": "nebula", "mag": 4.8},
    {"id": "IC 1805", "name": "Heart Nebula", "ra": 2.545, "dec": 61.450, "kind": "nebula", "mag": 6.5},
    {"id": "IC 1848", "name": "Soul Nebula", "ra": 2.850, "dec": 60.450, "kind": "nebula", "mag": 6.5},
    {"id": "NGC 1499", "name": "California Nebula", "ra": 4.047, "dec": 36.417, "kind": "nebula", "mag": 5.0},
]


def _clean_comment(text: str) -> str:
    sentence = text.strip().strip('"').split(".")[0].strip()
    if not sentence or len(sentence) > 48:
        return ""
    lowered = sentence.lower()
    if lowered[0].isdigit() or lowered.startswith(("excellent", "two ", "very ")):
        return ""
    return sentence


@lru_cache(maxsize=1)
def targets() -> list[dict]:
    path = Path(__file__).resolve().parent / "data" / "messier.csv"
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for record in csv.DictReader(handle):
            number = int(record["messier_id"])
            name = NAME_OVERRIDES.get(number) or _clean_comment(record.get("comments") or "")
            rows.append(
                {
                    "id": f"M{number}",
                    "name": name or f"Messier {number}",
                    "ra": float(record["ra"]),
                    "dec": float(record["dec"]),
                    "kind": TYPE_LABELS.get(record.get("type") or "", record.get("type") or ""),
                    "mag": float(record["v_mag"]) if record.get("v_mag") else None,
                }
            )
    rows.extend(EXTRAS)
    for item in rows:
        size = SIZE_ARCMIN.get(item["id"])
        if size is not None:
            item["size_arcmin"] = size
    rows.sort(key=lambda item: (not str(item["id"]).startswith("M"), _messier_number(item["id"]), item["id"]))
    return rows


def _messier_number(ident: str) -> int:
    if ident.startswith("M") and ident[1:].isdigit():
        return int(ident[1:])
    return 0


def locate(name: str) -> dict | None:
    """Match a session name such as ``M 42`` to a catalog object."""
    compact = _key(name)
    if len(compact) < 2:
        return None
    tokens = _tokens(name)
    for item in targets():
        ident = _key(item["id"])
        title = _key(item["name"])
        if compact in {ident, title} or ident in tokens or title in tokens:
            return item
    return None


def _tokens(name: str) -> set[str]:
    token = []
    tokens: set[str] = set()
    for char in name.lower():
        if char.isalnum():
            token.append(char)
        elif token:
            tokens.add("".join(token))
            token = []
    if token:
        tokens.add("".join(token))
    return tokens


def _key(name: str) -> str:
    return "".join(char for char in name.lower() if char.isalnum())


def search(query: str, limit: int = 40) -> list[dict]:
    needle = query.strip().lower()
    chosen = targets()
    if needle:
        chosen = [
            item
            for item in chosen
            if needle in item["id"].lower()
            or needle in item["name"].lower()
            or needle in item["kind"].lower()
        ]
    return chosen[:limit]
