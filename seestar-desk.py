#!/usr/bin/env python3
"""Start Seestar Desk on macOS, Linux, or Windows.

    macOS / Linux:  ./seestar-desk.py
    Windows:        py seestar-desk.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"


def _ensure_importable() -> None:
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    try:
        import seestar_desk.__main__  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-e", str(ROOT)])


def main() -> None:
    if sys.version_info < (3, 11):
        sys.exit("Seestar Desk needs Python 3.11 or newer.")
    _ensure_importable()
    from seestar_desk.__main__ import main as run

    run()


if __name__ == "__main__":
    main()
