"""
Edifier ES300 local control over Wi-Fi (reverse-engineered from Edifier Home 3.3.9).

Transport: raw TCP to <ip>:8080
  outbound (client->device): XOR_0xA5( json_utf8 )                 -- no header
  inbound  (device->client): EE DD FF EE | len(2,BE) | json , whole frame XOR_0xA5

Every command carries an "id". The device echoes that id back in:
  - a `settings` ack frame      {payload:"settings",    id:<id>, message:"success"}
  - a change-triggered status    {payload:"status_query", id:<id>, ...full state...}
both ~1s later. We read until the matching id arrives instead of polling on a timer.

Envelope: {"id":"<ms-timestamp>","payload":"settings","<field>":<obj>}
Note: the device drops a session after ~5s of silence, so reuse one connection --
the async context manager holds a single connection open for its lifetime.

Verified live: volume, transport, light (on/off, mode, brightness, warm/cool),
EQ (preset + 6-band custom), input source.

Usage:
    async with ES300("192.168.1.123") as device:
        status = await device.status()
        await device.volume(20)
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from edifier_es300.typing_ import (
    CommandResult,
    EqPreset,
    FrameData,
    LightColor,
    LightEffect,
    PlayerStatus,
    Source,
    Status,
)

KEY: int = 0xA5
FRAME_HEADER: bytes = b"\xee\xdd\xff\xee"


def _xor(data: bytes) -> bytes:
    return bytes(byte ^ KEY for byte in data)


def _uid() -> str:
    return str(int(time.time() * 1000))


class ES300:
    def __init__(self, host, port, name: str | None = None) -> None:
        self.name: str | None = name  # from discovery; refreshed by status if present
        self._host: str = host
        self._port: int = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._buffer = bytearray()  # decoded receive buffer; consumed bytes are trimmed
        self._offset: int = 0  # scan cursor into _buffer

    def __str__(self) -> str:
        return "%s  %s:%s" % (self.name or "?", self._host, self._port)

    @classmethod
    async def discover(cls, seconds: float = 3.0) -> list["ES300"]:
        """Broadcast-discover ES300 speakers on the LAN; one ES300 per device."""
        from .discovery import discover

        found = await discover(timeout=seconds)
        return [
            cls(
                host=device.host or device.address,
                port=device.port,
                name=device.name,
            )
            for device in found
        ]

    def _absorb_name(self, status: "Status | None") -> "Status | None":
        """Let a received status override the discovery name (when it carries one)."""
        if status is not None:
            name = status.raw.get("name")
            if name:
                self.name = name
        return status

    # --- connection lifecycle ---
    async def __aenter__(self) -> "ES300":
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = self._writer = None

    # --- framing ---
    async def _send_raw(self, message: FrameData) -> None:
        assert self._writer is not None, "not connected (use 'async with')"
        self._writer.write(_xor(json.dumps(message).encode()))
        await self._writer.drain()

    async def _frames(self, seconds: float) -> AsyncIterator[FrameData]:
        """Yield inbound JSON frames as they arrive, up to `seconds` seconds."""
        assert self._reader is not None, "not connected (use 'async with')"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        # Outer loop: keep pulling bytes off the socket until the deadline passes.
        while True:
            # Inner loop: drain every complete frame already sitting in the buffer.
            # A frame is HDR + 2-byte big-endian length + that many payload bytes;
            # we stop as soon as the next frame is missing or only partially received.
            while True:
                header_pos = self._buffer.find(FRAME_HEADER, self._offset)
                if header_pos < 0 or header_pos + 6 > len(self._buffer):
                    break  # no header yet, or length bytes not fully received
                payload_len = (self._buffer[header_pos + 4] << 8) | self._buffer[
                    header_pos + 5
                ]
                payload_start = header_pos + 6
                payload_end = payload_start + payload_len
                if payload_end > len(self._buffer):
                    break  # payload still arriving; wait for more bytes
                frame = self._buffer[payload_start:payload_end]
                self._offset = payload_end
                try:
                    yield json.loads(frame)
                except Exception:
                    pass
            # Drop consumed frames so the buffer only holds the unparsed tail --
            # otherwise it would grow for the life of a long-running connection.
            if self._offset:
                del self._buffer[: self._offset]
                self._offset = 0
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            try:
                chunk = await asyncio.wait_for(
                    self._reader.read(8192), timeout=remaining
                )
            except TimeoutError:
                return
            if not chunk:
                return
            self._buffer += _xor(chunk)

    # --- core request/response ---
    async def _command(
        self, field: str, obj: Any, payload: str = "settings", seconds: float = 5.0
    ) -> CommandResult:
        """Send a setting and wait for the id-matched ack + status.
        Returns (ok: bool, status: Status|None)."""
        message_id = _uid()
        await self._send_raw({"id": message_id, "payload": payload, field: obj})
        acked: bool | None = None
        status: Status | None = None
        async for frame in self._frames(seconds):
            if frame.get("id") != message_id:
                continue
            if frame.get("payload") == "settings":
                acked = frame.get("message") == "success"
            elif frame.get("payload") == "status_query":
                status = self._absorb_name(Status.from_frame(frame))
            if acked is not None and status is not None:
                break
        return bool(acked), status

    # --- commands ---
    async def status(self, seconds: float = 6.0) -> Status | None:
        """Request full state; return the parsed status (id-matched if possible)."""
        message_id = _uid()
        await self._send_raw({"id": message_id, "payload": "status_query"})
        fallback: Status | None = None
        async for frame in self._frames(seconds):
            if frame.get("payload") == "status_query":
                parsed = self._absorb_name(Status.from_frame(frame))
                if frame.get("id") == message_id:
                    return parsed
                fallback = parsed
        return fallback

    async def volume(self, level: int) -> CommandResult:
        return await self._command("player", {"volume": level})  # 0..30

    async def play(self) -> CommandResult:
        return await self._command(
            "player", {"playerStatus": int(PlayerStatus.PLAYING)}
        )

    async def pause(self) -> CommandResult:
        return await self._command(
            "player", {"playerStatus": int(PlayerStatus.STOPPED)}
        )

    async def play_pause_toggle(self) -> CommandResult:
        # Toggle by sending the opposite of the current state, like the app's button.
        current = await self.status()
        playing = current is not None and current.player_status is PlayerStatus.PLAYING
        target = PlayerStatus.STOPPED if playing else PlayerStatus.PLAYING
        return await self._command("player", {"playerStatus": int(target)})

    async def next_track(self) -> CommandResult:
        return await self._command("player", {"next": 1})

    async def previous_track(self) -> CommandResult:
        return await self._command("player", {"previous": 1})

    async def shutdown(self) -> CommandResult:
        # Powers the speaker fully off (Wi-Fi radio included); there is no remote
        # power-on, so it must be switched back on with the physical button.
        return await self._command("deviceShutdown", 1)

    async def timer_shutdown(self, minutes: int) -> CommandResult:
        # Sleep timer in minutes (0 = off; app presets 5/15/30/60/180). Preserve the
        # device's current timerIndex/timeRemaining and only change the duration,
        # mirroring the app.
        current = await self.status()
        timer = (current.timer_shutdown or {}) if current else {}
        return await self._command(
            "timerShutdown",
            {
                "timerIndex": timer.get("timerIndex", 1),
                "timeShutdown": minutes,
                "timeRemaining": timer.get("timeRemaining", 0),
            },
        )

    async def brightness(self, level: int) -> CommandResult:
        return await self._command("lightEffect", {"brightness": level})  # 0..100

    async def light_switch(self, enabled: bool) -> CommandResult:
        return await self._command("lightEffect", {"lightSwitch": int(enabled)})

    async def light_effect(self, effect: LightEffect | int) -> CommandResult:
        return await self._command("lightEffect", {"selectedIndex": int(effect)})

    async def light_color(self, color: LightColor) -> CommandResult:
        return await self._command("lightEffect", {"color": color.value})

    async def input_source(self, source: Source | int) -> CommandResult:
        return await self._command("inputSource", {"selectedIndex": int(source)})

    # EQ. Active EQ is chosen by selectedIndex (index into the speaker's preset list;
    # the last entry is the editable custom slot, auto-selected when you set gains).
    async def eq_preset(self, preset: EqPreset | int) -> CommandResult:
        current = await self.status()
        sound_index = current.sound_index if current else 2
        return await self._command(
            "soundEffect",
            {"soundIndex": sound_index, "selectedIndex": int(preset)},
        )

    async def eq_custom(
        self, gains: list[int]
    ) -> CommandResult:  # up to 6 ints in tenths of a dB (-30..30 = -3.0..+3.0 dB), for 62/250/1k/4k/8k/16k Hz
        current = await self.status()
        if current is None:
            return (False, None)
        sound_effect = current.raw["soundEffect"]
        diy = sound_effect["soundEffectDIY"]
        for index, gain in enumerate(gains):
            if index < len(diy["diyData"]):
                diy["diyData"][index]["gain"]["value"] = gain
        return await self._command(
            "soundEffect",
            {
                "soundIndex": sound_effect["soundIndex"],
                "selectedIndex": int(
                    EqPreset.CUSTOMIZED
                ),  # editing gains selects the custom slot
                "soundEffectDIY": diy,
            },
        )
