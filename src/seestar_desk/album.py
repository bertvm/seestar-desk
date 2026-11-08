"""Album listing and HTTP download from a Seestar."""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from seestar_desk.catalog import locate
from seestar_desk.errors import ScopeError


def parse_albums(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    folders: list[dict[str, Any]] = []
    for group in result.get("list") or []:
        if not isinstance(group, dict):
            continue
        for item in group.get("files") or []:
            if not isinstance(item, dict):
                continue
            folders.append(
                {
                    "name": item.get("name") or "",
                    "group": group.get("group_name") or "",
                    "count": item.get("count"),
                    "thumb": item.get("thn") or "",
                }
            )
    return [folder for folder in folders if folder["name"]]


def _as_file_list(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        for key in ("list", "files", "names"):
            if isinstance(result.get(key), list):
                return [item for item in result[key] if isinstance(item, dict)]
    return []


async def list_albums(session) -> list[dict[str, Any]]:
    response = await session.command("get_albums")
    return parse_albums(response.get("result"))


async def gallery_items(session) -> list[dict[str, Any]]:
    """One sky position for each session, from its name or from a FITS header."""
    items: list[dict[str, Any]] = []
    for folder in await list_albums(session):
        place = locate(str(folder.get("name") or ""))
        ra = float(place["ra"]) if place else None
        dec = float(place["dec"]) if place else None
        if ra is None:
            ra, dec = await _coords_from_fits(session, str(folder.get("name") or ""))
        items.append(
            {
                "name": folder.get("name") or "",
                "group": folder.get("group") or "",
                "count": folder.get("count"),
                "thumb": folder.get("thumb") or "",
                "ra": ra,
                "dec": dec,
                "kind": (place or {}).get("kind") or "",
            }
        )
    return items


async def _coords_from_fits(session, folder: str) -> tuple[float | None, float | None]:
    try:
        files = await list_folder(session, folder)
    except ScopeError:
        return None, None
    fit = next((item.get("name") for item in files if str(item.get("name") or "").lower().endswith((".fit", ".fits"))), "")
    if not fit:
        return None, None
    try:
        header = await _read_head(session.file_url(f"MyWorks/{_safe_part(folder)}/{_safe_part(str(fit))}"))
    except (ScopeError, urllib.error.URLError, TimeoutError, OSError):
        return None, None
    return fits_radec(header)


def fits_radec(header: bytes) -> tuple[float | None, float | None]:
    """Read J2000 hours and degrees from the start of a FITS file."""
    if not header.startswith(b"SIMPLE"):
        return None, None
    cards: dict[str, str] = {}
    for offset in range(0, len(header) - 79, 80):
        card = header[offset : offset + 80].decode("latin1", "replace")
        key = card[:8].strip()
        if key == "END":
            break
        if card[8:10] != "= ":
            continue
        cards[key] = card[10:].split("/", 1)[0].strip().strip("'\"")
    crval = _number(cards.get("CRVAL1", "")), _number(cards.get("CRVAL2", ""))
    if crval[0] is not None and crval[1] is not None:
        return crval[0] / 15.0, crval[1]
    ra = _number(cards.get("OBJCTRA", "") or cards.get("RA", ""))
    dec = _number(cards.get("OBJCTDEC", "") or cards.get("DEC", ""))
    if ra is None or dec is None:
        return None, None
    if abs(ra) > 24:
        ra = ra / 15.0
    return ra, dec


def _number(raw: str) -> float | None:
    text = raw.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    parts = text.replace(":", " ").split()
    if not parts:
        return None
    sign = -1.0 if parts[0].startswith("-") else 1.0
    try:
        values = [abs(float(part)) for part in parts[:3]]
    except ValueError:
        return None
    while len(values) < 3:
        values.append(0.0)
    return sign * (values[0] + values[1] / 60.0 + values[2] / 3600.0)


async def list_folder(session, folder: str) -> list[dict[str, Any]]:
    """Page through one album folder. The firmware keeps a single directory cursor."""
    safe = _safe_part(folder)
    directory = f"MyWorks/{safe}"
    async with session.album_lock:
        errors: list[str] = []
        # `dir` is the key that moves the cursor. `directory` is accepted on
        # firmware 9.75 but leaves the cursor at the storage root.
        for key in ("dir", "directory", "path"):
            try:
                page_count = await _page_count(session, directory, key)
            except ScopeError as exc:
                errors.append(str(exc))
                continue
            files = await _read_pages(session, page_count)
            if _is_storage_root(files):
                errors.append(f"{key} did not open {directory}")
                continue
            return await _with_previews(session, directory, files)
        raise ScopeError("Could not list that folder. " + "; ".join(errors))


async def _read_pages(session, page_count: int) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for page in range(max(page_count, 1)):
        batch = await _page_names(session, page)
        if not batch:
            break
        files.extend(batch)
        if page_count <= 1 and len(batch) < 20:
            break
    return files


def _is_storage_root(files: list[dict[str, Any]]) -> bool:
    names = {item.get("name") for item in files}
    return "MyWorks" in names and "darklibrary" in names


async def _page_count(session, directory: str, key: str) -> int:
    response = await session.command(
        "get_img_file_page_number",
        {key: directory, "skip_avi": False},
    )
    result = response.get("result")
    if isinstance(result, int):
        return result
    if isinstance(result, dict):
        for name in ("page_number", "pages", "page_count", "count"):
            if isinstance(result.get(name), int):
                return result[name]
    return 1


async def _with_previews(session, directory: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The page listing often includes only the FITS file. Offer the JPEG beside it."""
    known = {item.get("name") for item in files}
    extras: list[dict[str, Any]] = []
    for item in files:
        name = str(item.get("name") or "")
        if not name.lower().endswith(".fit"):
            continue
        stem = name[:-4]
        for candidate in (f"{stem}.jpg", f"{stem}_thn.jpg"):
            if candidate in known:
                continue
            try:
                response = await session.command("get_img_file_info", f"{directory}/{candidate}")
            except ScopeError:
                continue
            result = response.get("result") if isinstance(response.get("result"), dict) else {}
            extras.append(
                {
                    "name": candidate,
                    "size_k": result.get("size_k"),
                    "date": result.get("date") or "",
                    "is_dir": False,
                }
            )
            known.add(candidate)
    return files + extras


async def _page_names(session, page: int) -> list[dict[str, Any]]:
    last_error = ""
    for params in ({"page": page}, {"page_index": page}, {"page_num": page}):
        try:
            response = await session.command("get_img_file_page_name", params)
        except ScopeError as exc:
            last_error = str(exc)
            continue
        return [
            {
                "name": item.get("name") or "",
                "size_k": item.get("size_k"),
                "date": item.get("date") or "",
                "is_dir": bool(item.get("is_dir")),
            }
            for item in _as_file_list(response.get("result"))
            if item.get("name")
        ]
    if last_error:
        raise ScopeError(last_error)
    return []


async def delete_files(session, folder: str, names: list[str]) -> list[str]:
    """Remove files the user already copied. The firmware method is delete_image."""
    if not names:
        raise ScopeError("Choose at least one file.")
    folder_name = _safe_part(folder)
    removed: list[str] = []
    for name in names:
        filename = _safe_part(name)
        await session.command("delete_image", f"MyWorks/{folder_name}/{filename}")
        removed.append(filename)
    return removed


def download_files(session, folder: str, names: list[str], dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    folder_name = _safe_part(folder)
    for name in names:
        filename = _safe_part(name)
        url = session.file_url(f"MyWorks/{folder_name}/{filename}")
        target = dest / filename
        try:
            _stream(url, target)
        except urllib.error.HTTPError as exc:
            raise ScopeError(f"{filename} was not on the telescope ({exc.code}).") from exc
        except urllib.error.URLError as exc:
            raise ScopeError(f"Could not download {filename}: {exc.reason}") from exc
        saved.append(str(target))
    return saved


async def _read_head(url: str, limit: int = 23040) -> bytes:
    def read() -> bytes:
        request = urllib.request.Request(url, headers={"Range": f"bytes=0-{limit - 1}"})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read(limit)

    return await asyncio.to_thread(read)


def _stream(url: str, target: Path) -> None:
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as handle:
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            handle.write(chunk)


def _safe_part(value: str) -> str:
    cleaned = value.replace("\\", "/").strip().strip("/")
    if not cleaned or cleaned in {".", ".."} or ".." in cleaned.split("/"):
        raise ScopeError("That file name is not allowed.")
    return cleaned
