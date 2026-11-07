"""Open the desk in a browser."""

from __future__ import annotations

import argparse
import asyncio
import webbrowser

from aiohttp import web

from seestar_desk.fake_scope import FakeScope
from seestar_desk.server import Desk, build_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Operate a Seestar from your browser.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8731)
    parser.add_argument("--demo", action="store_true", help="Run against a simulated S50 Pro.")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.host, args.port, args.demo, not args.no_browser))


async def run(host: str, port: int, demo: bool, open_browser: bool) -> None:
    desk = Desk()
    fake: FakeScope | None = None
    if demo:
        fake = FakeScope()
        await fake.start()
        desk.demo_only = True
        desk.pin(fake.address())
    app = build_app(desk)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    url = f"http://{host}:{port}/"
    print(f"Seestar Desk is at {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        if fake is not None:
            await fake.close()


if __name__ == "__main__":
    main()
