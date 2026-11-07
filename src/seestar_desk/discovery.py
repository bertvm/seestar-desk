"""Find Seestar telescopes announcing themselves on the local network."""

from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass, field


DISCOVERY_PORT = 4720


@dataclass
class FoundScope:
    host: str
    name: str = ""
    model: str = ""
    ssid: str = ""
    raw: dict = field(default_factory=dict)
    control_port: int = 4700
    image_port: int = 4800
    wide_port: int = 4804
    http_port: int = 80

    @property
    def id(self) -> str:
        if self.control_port == 4700:
            return self.host
        return f"{self.host}:{self.control_port}"


def broadcast_targets() -> list[tuple[str, int]]:
    targets = [("255.255.255.255", DISCOVERY_PORT)]
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))
        local = probe.getsockname()[0]
        probe.close()
        parts = local.split(".")
        if len(parts) == 4 and not local.startswith("127."):
            parts[3] = "255"
            targets.append((".".join(parts), DISCOVERY_PORT))
    except OSError:
        pass
    return targets


def parse_beacon(payload: bytes, sender: str) -> FoundScope:
    text = payload.decode("utf-8", errors="replace").strip()
    data: dict = {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            data = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            data = {}
    result = data.get("result") if isinstance(data.get("result"), dict) else data
    host = str(result.get("ip") or result.get("host") or sender)
    name = str(result.get("name") or result.get("device_name") or result.get("ssid") or "")
    model = str(result.get("model") or result.get("product_model") or result.get("device") or "")
    ssid = str(result.get("ssid") or "")
    return FoundScope(host=host, name=name, model=model, ssid=ssid, raw=data)


async def discover(
    timeout: float = 2.5,
    targets: list[tuple[str, int]] | None = None,
) -> list[FoundScope]:
    """Broadcast scan_iscope and collect every telescope that answers."""
    loop = asyncio.get_running_loop()
    found: dict[str, FoundScope] = {}

    class Protocol(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[no-untyped-def]
            scope = parse_beacon(data, addr[0])
            found[scope.host] = scope

        def error_received(self, exc: Exception) -> None:
            return None

    transport, _protocol = await loop.create_datagram_endpoint(
        Protocol,
        local_addr=("0.0.0.0", 0),
        allow_broadcast=True,
    )
    message = json.dumps({"id": 1, "method": "scan_iscope", "params": ""}).encode("utf-8") + b"\r\n"
    try:
        for target in targets or broadcast_targets():
            try:
                transport.sendto(message, target)
            except OSError:
                continue
        await asyncio.sleep(timeout)
    finally:
        transport.close()
    return list(found.values())
