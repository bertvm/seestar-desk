"""Binary frame reader for Seestar image ports 4800 and 4804."""

from __future__ import annotations

import io
import json
import math
import struct
import sys
import zlib
from array import array
from dataclasses import dataclass
from typing import Any

from PIL import Image

MAGIC = 0x03C3
MAX_PAYLOAD = 80_000_000

# img_type values observed on the image socket.
ACK = 0
PREVIEW = 1
JPEG = 4
RAW = 5


@dataclass
class ImageFrame:
    img_type: int
    data_type: int
    width: int
    height: int
    payload: bytes
    big_endian: bool
    image_id: int


def build_frame(
    payload: bytes,
    img_type: int,
    width: int,
    height: int,
    data_type: int = 3,
    big_endian: bool = False,
    image_id: int = 1,
) -> bytes:
    header = bytearray(80)
    struct.pack_into(">HHHI", header, 0, MAGIC, 2, 80, len(payload))
    header[12] = 1 if big_endian else 0
    header[13] = img_type
    header[14] = data_type
    header[15] = image_id & 0xFF
    struct.pack_into(">HHHH", header, 16, width & 0xFFFF, height & 0xFFFF, 0, 0)
    struct.pack_into(">HH", header, 28, image_id & 0xFFFF, 0)
    return bytes(header) + payload


class FrameParser:
    """Synchronise a mixed stream of JSON lines and 80-byte image frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[str, Any]]:
        self._buf.extend(data)
        items: list[tuple[str, Any]] = []
        while self._buf:
            self._skip_space()
            if not self._buf:
                break
            if self._buf[0] == ord("{"):
                newline = self._buf.find(b"\n")
                if newline < 0:
                    break
                raw = bytes(self._buf[:newline]).strip()
                del self._buf[: newline + 1]
                if raw:
                    items.append(("json", json.loads(raw)))
                continue
            if len(self._buf) >= 2 and struct.unpack_from(">H", self._buf, 0)[0] != MAGIC:
                marker = self._buf.find(MAGIC.to_bytes(2, "big"))
                brace = self._buf.find(b"{")
                candidates = [index for index in (marker, brace) if index > 0]
                if not candidates:
                    del self._buf[:-1]
                    break
                del self._buf[: min(candidates)]
                continue
            if len(self._buf) < 6:
                break
            header_size = struct.unpack_from(">H", self._buf, 4)[0]
            if header_size < 34 or header_size > 512:
                del self._buf[0]
                continue
            if len(self._buf) < header_size:
                break
            length = struct.unpack_from(">I", self._buf, 6)[0]
            if length > MAX_PAYLOAD:
                del self._buf[0]
                continue
            total = header_size + length
            if len(self._buf) < total:
                break
            header = bytes(self._buf[:header_size])
            payload = bytes(self._buf[header_size:total])
            del self._buf[:total]
            items.append(("frame", _parse_header(header, payload)))
        return items


def _skip_space_buf(buf: bytearray) -> None:
    index = 0
    while index < len(buf) and buf[index] in (9, 10, 13, 32):
        index += 1
    if index:
        del buf[:index]


def _parse_header(header: bytes, payload: bytes) -> ImageFrame:
    img_type = header[13]
    data_type = header[14]
    width, height = struct.unpack_from(">HH", header, 16)
    image_id = struct.unpack_from(">H", header, 28)[0]
    return ImageFrame(
        img_type=img_type,
        data_type=data_type,
        width=width,
        height=height,
        payload=payload,
        big_endian=header[12] == 1,
        image_id=image_id,
    )


# Bind the method after the helper exists.
FrameParser._skip_space = lambda self: _skip_space_buf(self._buf)  # type: ignore[method-assign]


def to_jpeg(frame: ImageFrame, max_edge: int = 960, quality: int = 82) -> bytes | None:
    """Turn a firmware frame into a JPEG the browser can show."""
    if frame.img_type == ACK or not frame.payload:
        return None
    if frame.img_type == JPEG or frame.payload.startswith(b"\xff\xd8"):
        return _fit_jpeg(frame.payload, max_edge, quality)
    payload = _maybe_unzip(frame.payload, frame.width, frame.height, frame.img_type)
    if frame.img_type == PREVIEW or (
        frame.width and frame.height and len(payload) == frame.width * frame.height * 2
    ):
        return _bayer_to_jpeg(payload, frame.width, frame.height, frame.big_endian, max_edge, quality)
    if frame.width and frame.height:
        return _rgb16_to_jpeg(payload, frame.width, frame.height, frame.big_endian, max_edge, quality)
    return None


def preview_and_display(frame: ImageFrame) -> tuple[bytes | None, bytes | None]:
    """A desk-sized JPEG and a full frame up to 4K on the long edge."""
    display = to_jpeg(frame, max_edge=3840, quality=92)
    if not display:
        return None, None
    return _fit_jpeg(display, 960, 82), display


def _maybe_unzip(payload: bytes, width: int, height: int, img_type: int) -> bytes:
    expected = width * height * (6 if img_type == RAW else 2)
    if expected and len(payload) == expected:
        return payload
    start = payload.find(b"PK\x03\x04")
    if start < 0 or len(payload) < start + 30:
        return payload
    _version, _flag, method, _time, _date, _crc, comp_size, _uncomp, name_len, extra_len = struct.unpack_from(
        "<HHHHHIIIHH", payload, start + 4
    )
    data_at = start + 30 + name_len + extra_len
    if method == 0:
        return payload[data_at : data_at + comp_size] if comp_size else payload[data_at:]
    if method != 8:
        return payload
    compressed = payload[data_at : data_at + comp_size] if comp_size else payload[data_at:]
    try:
        return zlib.decompress(compressed, -15)
    except zlib.error:
        return payload


def _u16_array(payload: bytes, count: int, big_endian: bool) -> array:
    pixels = array("H")
    pixels.frombytes(payload[: count * 2])
    if (sys.byteorder == "big") != big_endian:
        pixels.byteswap()
    return pixels


def _stretch_limits(pixels: array, stride: int) -> tuple[int, int]:
    return _limits(list(pixels[::stride]) or [0])


def _limits(sample: list[int]) -> tuple[int, int]:
    if not sample:
        return 0, 1
    ordered = sorted(sample)
    black = ordered[max(0, len(ordered) // 40)]
    white = ordered[min(len(ordered) - 1, int(len(ordered) * 0.995))]
    if white <= black:
        return black, black
    return black, white


def _scale(value: int, black: int, white: int) -> int:
    if white <= black:
        return 0
    if value <= black:
        return 0
    if value >= white:
        return 255
    return int((value - black) * 255 / (white - black))


def _save_jpeg(image: Image.Image, quality: int = 82) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, subsampling=0 if quality >= 90 else 2)
    return buffer.getvalue()


def _fit_jpeg(payload: bytes, max_edge: int, quality: int) -> bytes:
    try:
        image = Image.open(io.BytesIO(payload))
        image.load()
    except OSError:
        return payload
    edge = max(image.size)
    if edge <= max_edge:
        return payload
    image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return _save_jpeg(image.convert("RGB"), quality)


def _rgb16_to_jpeg(payload: bytes, width: int, height: int, big_endian: bool, max_edge: int, quality: int = 82) -> bytes:
    needed = width * height * 3
    pixels = _u16_array(payload, needed, big_endian)
    step = max(1, math.ceil(max(width, height) / max_edge))
    out_w = max(1, width // step)
    out_h = max(1, height // step)
    black, white = _stretch_limits(pixels, max(1, len(pixels) // 4000))
    raw = bytearray(out_w * out_h * 3)
    cursor = 0
    for y in range(out_h):
        row = (y * step) * width
        for x in range(out_w):
            index = (row + x * step) * 3
            raw[cursor] = _scale(pixels[index], black, white)
            raw[cursor + 1] = _scale(pixels[index + 1], black, white)
            raw[cursor + 2] = _scale(pixels[index + 2], black, white)
            cursor += 3
    return _save_jpeg(Image.frombytes("RGB", (out_w, out_h), bytes(raw)), quality)


def _bayer_to_jpeg(payload: bytes, width: int, height: int, big_endian: bool, max_edge: int, quality: int = 82) -> bytes:
    if width < 2 or height < 2:
        return _rgb16_to_jpeg(payload, max(width, 1), max(height, 1), big_endian, max_edge, quality)
    pixels = _u16_array(payload, width * height, big_endian)
    step = max(2, math.ceil(max(width, height) / max_edge))
    if step % 2:
        step += 1
    out_w = max(1, width // step)
    out_h = max(1, height // step)
    shared = _stretch_limits(pixels, max(1, len(pixels) // 4000))
    reds: list[int] = []
    greens: list[int] = []
    blues: list[int] = []
    sample_step = max(2, step * 2)
    for sy in range(0, height - 1, 2):
        row = sy * width
        for sx in range(0, width - 1, sample_step):
            greens.append(pixels[row + sx])
            reds.append(pixels[row + sx + 1])
            blues.append(pixels[row + width + sx])
            greens.append(pixels[row + width + sx + 1])
    red_limits = _limits(reds)
    green_limits = _limits(greens)
    blue_limits = _limits(blues)
    if red_limits[1] <= red_limits[0]:
        red_limits = shared
    if green_limits[1] <= green_limits[0]:
        green_limits = shared
    if blue_limits[1] <= blue_limits[0]:
        blue_limits = shared
    raw = bytearray(out_w * out_h * 3)
    cursor = 0
    for y in range(out_h):
        sy = min(height - 2, y * step)
        if sy % 2:
            sy -= 1
        for x in range(out_w):
            sx = min(width - 2, x * step)
            if sx % 2:
                sx -= 1
            # Seestar previews are GRBG: green, red on the even row, then blue, green.
            green = (pixels[sy * width + sx] + pixels[(sy + 1) * width + sx + 1]) // 2
            red = pixels[sy * width + sx + 1]
            blue = pixels[(sy + 1) * width + sx]
            raw[cursor] = _scale(red, *red_limits)
            raw[cursor + 1] = _scale(green, *green_limits)
            raw[cursor + 2] = _scale(blue, *blue_limits)
            cursor += 3
    image = Image.frombytes("RGB", (out_w, out_h), bytes(raw))
    # The sensor's long axis is upright. Turn it so an open arm fills the wide picture.
    if image.height > image.width:
        image = image.transpose(Image.Transpose.ROTATE_270)
    return _save_jpeg(image, quality)
