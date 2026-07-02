"""
Auto-discovery for Edifier ES300 speakers on the local network.

The Edifier Home app finds speakers with a UDP broadcast handshake on port 6000
(not mDNS -- the app's Bonjour libs are only for AirPlay):

  1. The app broadcasts, to 255.255.255.255:6000, the plaintext query:
         {"firm":"EDF","phoneOS":1,"protocol":1}
  2. Each speaker replies (on port 6000) with a plaintext `AudioWlan` JSON:
         {"encryption":<xor key>, "firm":"EDF", "protocol":1, "product":..., "player":...,
          "info":{"host":<ip>, "port":8080, "name":..., "uuid":..., "bluetoothMac":..., "wifiMac":...}}
  3. The app then opens the TCP control channel at info.host:info.port.

Unlike the control channel, discovery frames are NOT XOR-obfuscated -- both the
query and the reply are plain UTF-8 JSON.

Usage:
    devices = await discover()
    if devices:
        async with ES300(devices[0].host, devices[0].port) as speaker:
            ...

Run standalone:
    python -m edifier_es300.discovery
"""

import asyncio
import json
import logging
from dataclasses import dataclass

from edifier_es300.typing_ import FrameData

logger = logging.getLogger(__name__)

DISCOVERY_PORT: int = 6000
BROADCAST_ADDR: str = "255.255.255.255"
DISCOVERY_QUERY: FrameData = {"firm": "EDF", "phoneOS": 1, "protocol": 1}


@dataclass
class DiscoveredDevice:
    """A speaker that answered the discovery broadcast."""

    name: str | None
    host: str | None  # info.host -- the control-channel IP
    port: int | None  # info.port -- the control-channel TCP port (8080)
    uuid: int | None
    bluetooth_mac: str | None
    wifi_mac: str | None
    encryption: int | None  # XOR key the control channel expects (0xA5 on ES300)
    address: str  # UDP source IP of the reply (host fallback)
    raw: FrameData  # the full AudioWlan reply

    @classmethod
    def from_reply(cls, data: FrameData, source_ip: str) -> "DiscoveredDevice":
        info = data.get("info") or {}
        return cls(
            name=info.get("name"),
            host=info.get("host") or source_ip,
            port=info.get("port"),
            uuid=info.get("uuid"),
            bluetooth_mac=info.get("bluetoothMac"),
            wifi_mac=info.get("wifiMac"),
            encryption=data.get("encryption"),
            address=source_ip,
            raw=data,
        )

    def __str__(self) -> str:
        return "%s  %s:%s  uuid=%s  mac address=%s  encryption=%s" % (
            self.name or "?",
            self.host,
            self.port,
            self.uuid,
            self.wifi_mac,
            self.encryption,
        )


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """Collects AudioWlan replies, ignoring our own echoed broadcast."""

    def __init__(self) -> None:
        self.replies: list[tuple[FrameData, str]] = []

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            obj = json.loads(data.decode("utf-8"))
        except Exception:
            return
        # Our own broadcast (the query) has no "info"; only real replies do.
        if isinstance(obj, dict) and obj.get("info"):
            self.replies.append((obj, addr[0]))


async def discover(timeout: float = 3.0, broadcasts: int = 3) -> list[DiscoveredDevice]:
    """Broadcast the discovery query and collect replies for `timeout` seconds.

    Sends the query `broadcasts` times (spread across the window) since UDP is
    lossy. Returns one DiscoveredDevice per speaker, de-duplicated by host.
    """
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        _DiscoveryProtocol,
        local_addr=("0.0.0.0", DISCOVERY_PORT),
        allow_broadcast=True,
    )
    try:
        payload = json.dumps(DISCOVERY_QUERY).encode("utf-8")
        deadline = loop.time() + timeout
        gap = timeout / max(1, broadcasts)
        for _ in range(max(1, broadcasts)):
            transport.sendto(payload, (BROADCAST_ADDR, DISCOVERY_PORT))
            await asyncio.sleep(min(gap, max(0.0, deadline - loop.time())))
        remaining = deadline - loop.time()
        if remaining > 0:
            await asyncio.sleep(remaining)
    finally:
        transport.close()

    devices: dict[str, DiscoveredDevice] = {}
    for reply, source_ip in protocol.replies:
        device = DiscoveredDevice.from_reply(reply, source_ip)
        devices[device.host or source_ip] = device  # dedupe by host
    return list(devices.values())


async def discover_one(timeout: float = 3.0) -> DiscoveredDevice | None:
    """Return the first speaker found, or None."""
    devices = await discover(timeout=timeout)
    return devices[0] if devices else None


async def _main() -> None:
    devices = await discover()
    if not devices:
        logger.warning("no ES300 speakers found (same Wi-Fi as the speaker?)")
        return
    for device in devices:
        logger.info("%s", device)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(_main())
