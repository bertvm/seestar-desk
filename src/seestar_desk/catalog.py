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
    rows.sort(key=lambda item: (not str(item["id"]).startswith("M"), _messier_number(item["id"]), item["id"]))
    return rows


def _messier_number(ident: str) -> int:
    if ident.startswith("M") and ident[1:].isdigit():
        return int(ident[1:])
    return 0


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
