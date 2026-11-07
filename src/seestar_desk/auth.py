"""Sign the firmware handshake the current Seestar app uses.

Firmware that answers ``get_verify_str`` ignores every other command until
the client returns an RSA-SHA1 signature of that challenge. The key is the
one embedded in the official Android app. This module never ships that key.
Put a PEM you extracted yourself at one of the paths in :func:`find_key`.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

MISSING_KEY = (
    "The telescope is asking for the current Seestar app. "
    "Save the interoperability key as ~/.seestar-desk/seestar.pem "
    "(or set SEESTAR_KEY_PATH). From an Android APK you already have: "
    "python -m seestar_desk.extract_pem path/to/seestar.apk"
)


def app_key_paths() -> list[Path]:
    """Private-key files shipped inside the Seestar Mac or iOS app bundle."""
    names = ("my_private.pem",)
    roots = [
        Path("/Applications/Seestar.app"),
        Path.home() / "Applications" / "Seestar.app",
    ]
    found: list[Path] = []
    for root in roots:
        wrapper = root / "Wrapper" / "Seestar.app"
        for name in names:
            found.append(wrapper / name)
            found.append(root / name)
    return found


def find_key() -> Path | None:
    """Return the first PEM that exists, or None."""
    candidates: list[Path] = []
    env = os.environ.get("SEESTAR_KEY_PATH")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(Path.cwd() / "seestar.pem")
    candidates.append(Path.home() / ".seestarpy" / "seestar.pem")
    candidates.append(Path.home() / ".seestar-desk" / "seestar.pem")
    candidates.extend(app_key_paths())
    for path in candidates:
        if path.is_file():
            return path
    return None


def sign_challenge(key_path: Path, challenge: str) -> str:
    """Return a base64 RSA-SHA1 PKCS#1 v1.5 signature of the challenge."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    signature = private_key.sign(
        challenge.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA1(),
    )
    return base64.b64encode(signature).decode("ascii")
