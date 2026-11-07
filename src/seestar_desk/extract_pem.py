"""Copy a PEM private key out of a Seestar Android APK the user already has.

The desk does not download an APK and does not include a key. Run:

    python -m seestar_desk.extract_pem path/to/seestar.apk
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

SO_PATHS = (
    "lib/arm64-v8a/libopenssllib.so",
    "lib/armeabi-v7a/libopenssllib.so",
)
PEM_RE = re.compile(r"-----BEGIN PRIVATE KEY-----[\s\S]*?-----END PRIVATE KEY-----")
DEFAULT_OUTPUT = Path.home() / ".seestar-desk" / "seestar.pem"


def _strings(data: bytes) -> str:
    runs: list[str] = []
    current = bytearray()
    for byte in data:
        if 32 <= byte <= 126:
            current.append(byte)
            continue
        if len(current) >= 4:
            runs.append(current.decode("ascii"))
        current.clear()
    if len(current) >= 4:
        runs.append(current.decode("ascii"))
    return "\n".join(runs)


def extract_pem_from_apk(apk_path: Path) -> list[str]:
    keys: set[str] = set()
    with zipfile.ZipFile(apk_path) as archive:
        names = set(archive.namelist())
        found = [path for path in SO_PATHS if path in names]
        if not found:
            return []
        for path in found:
            keys.update(PEM_RE.findall(_strings(archive.read(path))))
    return sorted(keys)


def write_key(pem: str, out_path: Path = DEFAULT_OUTPUT) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(pem if pem.endswith("\n") else pem + "\n")
    try:
        out_path.chmod(0o600)
    except OSError:
        pass
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract the Seestar app key from an APK you supply.")
    parser.add_argument("apk", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if not args.apk.is_file():
        print(f"APK not found: {args.apk}", file=sys.stderr)
        return 2
    try:
        keys = extract_pem_from_apk(args.apk)
    except zipfile.BadZipFile:
        print(f"Not a valid APK: {args.apk}", file=sys.stderr)
        return 2
    if not keys:
        print("No private key found in that APK.", file=sys.stderr)
        return 1
    path = write_key(keys[0], args.output)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
