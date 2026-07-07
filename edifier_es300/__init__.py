"""
Edifier ES300 local control over Wi-Fi (reverse-engineered from Edifier Home 3.3.9).

Transport: raw TCP to <ip>:8080
  outbound (client->device): XOR_0xA5( json_utf8 )                 -- no header
  inbound  (device->client): EE DD FF EE | len(2,BE) | json , whole frame XOR_0xA5

Every command carries an "id". The device echoes that id back in:
  - a `settings` ack frame      {payload:"settings",    id:<id>, message:"success"}
  - a change-triggered status    {payload:"status_query", id:<id>, ...full state...}
both ~1s later. A background task consumes every inbound frame and resolves the
Future the id-matched command is awaiting.

Envelope: {"id":"<uuid>","payload":"settings","<field>":<obj>}
Note: the device drops a session after ~5s of silence, so reuse one connection --
the async context manager holds a single connection open for its lifetime.

Verified live: volume, transport, light (on/off, mode, brightness, warm/cool),
EQ (preset + 6-band custom), input source.

Usage:
    async with ES300("192.168.1.123") as device:
        status = await device.status()
        await device.volume(20)          # returns the ack frame, raises CommandFailed
"""

import asyncio
import dataclasses
import json
import logging
import random
import time
from typing import Callable

from edifier_es300.typing_ import (
    EqPreset,
    FrameData,
    LightColor,
    LightEffect,
    PlayerStatus,
    Source,
    Status,
)

FRAME_HEADER = b"\xee\xdd\xff\xee"
KEY = 0xA5
logger = logging.getLogger(__name__)


__all__ = [
    "ES300",
    "EqPreset",
    "LightColor",
    "LightEffect",
    "PlayerStatus",
    "Source",
    "Status",
    "FrameData",
]


def _xor(data: bytes) -> bytes:
    return bytes(byte ^ KEY for byte in data)


class EndOfStream(Exception):
    pass


class CommandFailed(Exception):
    pass


async def _sync_to_header(byte_iter):
    pos = 0
    async for byte in byte_iter:
        if byte == FRAME_HEADER[pos]:
            pos += 1
            if pos == len(FRAME_HEADER):
                return  # matched; iterator now sits at the length byte
        elif byte == FRAME_HEADER[0]:
            pos = 1  # the mismatching byte restarts the header
        else:
            pos = 0


async def _read_frame(byte_iter):
    await _sync_to_header(byte_iter)
    hi = await anext(byte_iter)
    lo = await anext(byte_iter)
    length = (hi << 8) | lo  # 2-byte big-endian
    return bytes([await anext(byte_iter) for _ in range(length)])


async def _iter_byte(reader):
    while (buffer := await reader.read(8192)) != b"":
        for b in buffer:
            yield b ^ KEY
    else:
        raise EndOfStream()


type FutureStorage = dict[str, asyncio.Future]


def _uid():
    return str(int(time.time() * 1000) + random.randint(1000, 9999))


@dataclasses.dataclass
class CommandMessage:
    command: str | None = None
    value: FrameData | int | None = None
    id: str = dataclasses.field(default_factory=_uid)
    payload: str = "settings"  # "status_query" for a bare status request

    def __bytes__(self):
        return _xor(str(self).encode())

    def __str__(self):
        body = {"id": self.id, "payload": self.payload}
        if self.command is not None:
            body[self.command] = self.value
        return json.dumps(body)

    def encode(self):
        return bytes(self)


class ES300:
    def __init__(self, host, port=8080, name=None, wait_task_timeout=5):
        self.name = name  # from discovery; refreshed by status if present
        self._host = host
        self._port = port
        self._command_storage: FutureStorage = {}
        self._consume_task: asyncio.Task | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._latest_status: Status | None = None
        self._wait_task_timeout = wait_task_timeout
        self._status_callbacks: list[Callable[[Status], None]] = []
        self._heartbeat_callbacks: list[Callable[[FrameData], None]] = []

    def __str__(self):
        return "%s  %s:%s" % (self.name or "?", self._host, self._port)

    def status_callback(self, func: Callable[[Status], None]):
        self._status_callbacks.append(func)
        return func

    def heartbeat_callback(self, func: Callable[[FrameData], None]):
        self._heartbeat_callbacks.append(func)
        return func

    def remove_status_callback(self, func: Callable[[Status], None]):
        try:
            self._status_callbacks.remove(func)
        except ValueError:
            pass

    def remove_heartbeat_callback(self, func: Callable[[FrameData], None]):
        try:
            self._heartbeat_callbacks.remove(func)
        except ValueError:
            pass

    async def _exec_callback(
        self, callbacks: list[Callable], value: FrameData | Status
    ):
        # Iterate a copy so a callback may remove itself during dispatch.
        for func in list(callbacks):
            await func(value)

    async def _exec_status_callbacks(self, status: Status):
        await self._exec_callback(self._status_callbacks, status)

    async def _exec_heartbeat_callback(self, data: FrameData):
        await self._exec_callback(self._heartbeat_callbacks, data)

    @classmethod
    async def discover(cls, seconds: float = 3.0) -> list["ES300"]:
        """Broadcast-discover ES300 speakers on the LAN; one ES300 per device."""
        from .discovery import discover

        found = await discover(timeout=seconds)
        return [
            cls(host=device.host or device.address, port=device.port, name=device.name)
            for device in found
        ]

    def _absorb_name(self, status: Status | None) -> Status | None:
        """Let a received status override the discovery name (when it carries one)."""
        try:
            self.name = status.raw["name"] or self.name  # ty: ignore[unresolved-attribute]
        except (AttributeError, KeyError):
            pass  # status is None, or the frame carries no name
        return status

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def open(self):
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port
        )
        self._consume_task = asyncio.create_task(self._start(self._reader))

    async def close(self):
        try:
            async with asyncio.timeout(self._wait_task_timeout):
                await self._wait_for_in_flight_commands_to_finish()
        except TimeoutError:
            logger.warning("in-flight commands did not finish before close")
        finally:
            assert self._writer is not None
            assert self._consume_task is not None
            self._writer.close()
            await self._writer.wait_closed()
            await self._consume_task

    async def _wait_for_in_flight_commands_to_finish(self):
        while self._command_storage:
            await asyncio.sleep(0.5)

    async def _handle_heart_beat(self, payload):
        logger.info("heartbeat received: %r", payload)
        await self._exec_heartbeat_callback(payload)

    async def _handle_status_query(self, payload):
        try:
            status = self._absorb_name(Status.from_frame(payload))
        except (KeyError, TypeError):
            logger.info("unparseable status frame: %r", payload)
            return
        if status is None:
            return
        self._latest_status = status
        await self._exec_status_callbacks(status)
        try:
            message_id = payload["id"]
            future = self._command_storage.pop(message_id)
            future.set_result(self._latest_status)
        except KeyError:
            pass
        except asyncio.InvalidStateError:
            pass  # future already resolved/cancelled

    async def _handle_settings(self, message_id, payload, full_payload):
        try:
            future = self._command_storage.pop(message_id)
            if payload == "success":
                future.set_result(full_payload)
            else:
                future.set_exception(CommandFailed(repr(full_payload)))
        except KeyError:
            logger.info(
                "unknown command result received, possibly from previous sessions"
            )
            pass

    async def _handle_payload(self, raw_payload):
        logger.debug("raw payload: %r", raw_payload)
        try:
            payload = json.loads(raw_payload)
            match payload:
                case {"payload": "heart_beat"}:
                    await self._handle_heart_beat(payload)
                case {"payload": "status_query"}:
                    await self._handle_status_query(payload)
                case {"id": message_id, "payload": "settings", "message": message}:
                    await self._handle_settings(message_id, message, payload)
                case _:
                    logger.error("unknown message: %r", payload)
        except ValueError:
            pass

    async def _start(self, reader):
        try:
            byte_iter = aiter(_iter_byte(reader))
            while True:
                payload = await _read_frame(byte_iter)
                await self._handle_payload(payload)
        except EndOfStream:
            logger.info("device closed the connection")
            self._fail_pending()

    def _fail_pending(self):
        # On EOF, fail everything still awaiting so callers don't hang forever.
        while self._command_storage:
            _, future = self._command_storage.popitem()
            try:
                future.set_exception(EndOfStream())
            except asyncio.InvalidStateError:
                pass  # future already resolved/cancelled

    async def _write_command(self, message: CommandMessage):
        assert self._writer is not None, "not connected (use 'async with')"
        self._writer.write(message.encode())
        await self._writer.drain()

    async def _command(self, message):
        logger.info("sending command: %s", message)
        future = self._command_storage[message.id] = asyncio.Future()
        await self._write_command(message)
        return await future

    async def status(self) -> Status | None:
        return await self._command(CommandMessage(payload="status_query"))

    async def volume(self, level: int):
        return await self._command(CommandMessage("player", {"volume": level}))

    async def play(self):
        return await self._command(
            CommandMessage("player", {"playerStatus": int(PlayerStatus.PLAYING)})
        )

    async def pause(self):
        return await self._command(
            CommandMessage("player", {"playerStatus": int(PlayerStatus.STOPPED)})
        )

    async def play_pause_toggle(self):
        # Toggle by sending the opposite of the current state, like the app's button.
        current = await self.status()
        try:
            target = current.player_status ^ 1  # ty: ignore[unresolved-attribute]
        except AttributeError:
            # current is None
            target = int(PlayerStatus.PLAYING)

        return await self._command(CommandMessage("player", {"playerStatus": target}))

    async def next_track(self):
        return await self._command(CommandMessage("player", {"next": 1}))

    async def previous_track(self):
        return await self._command(CommandMessage("player", {"previous": 1}))

    async def shutdown(self):
        # Powers the speaker fully off (Wi-Fi radio included); there is no remote
        # power-on, so it must be switched back on with the physical button.
        return await self._command(CommandMessage("deviceShutdown", 1))

    async def timer_shutdown(self, minutes: int):
        # Sleep timer in minutes (0 = off; app presets 5/15/30/60/180). Preserve the
        # device's current timerIndex/timeRemaining and only change the duration.
        return await self._command(
            CommandMessage(
                "timerShutdown",
                {
                    "timeShutdown": minutes,
                },
            )
        )

    async def brightness(self, level: int):
        return await self._command(
            CommandMessage("lightEffect", {"brightness": level})  # 0..100
        )

    async def light_switch(self, enabled: bool):
        return await self._command(
            CommandMessage("lightEffect", {"lightSwitch": int(enabled)})
        )

    async def light_effect(self, effect: LightEffect | int):
        return await self._command(
            CommandMessage("lightEffect", {"selectedIndex": int(effect)})
        )

    async def light_color(self, color: LightColor):
        return await self._command(
            CommandMessage("lightEffect", {"color": color.value})
        )

    async def input_source(self, source: Source | int):
        return await self._command(
            CommandMessage("inputSource", {"selectedIndex": int(source)})
        )

    # EQ. Active EQ is chosen by selectedIndex (index into the speaker's preset list;
    # the last entry is the editable custom slot, auto-selected when you set gains).
    async def eq_preset(self, preset: EqPreset | int):
        return await self._command(
            CommandMessage(
                "soundEffect", {"soundIndex": 2, "selectedIndex": int(preset)}
            )
        )

    def _eq_payload(self, eq_gains: tuple[int, int, int, int, int, int]):
        eq_values = (62, 250, 1000, 4000, 8000, 16000)

        def _make_diy_dict(eq_value: int, eq_gain: int) -> dict:
            return {"fPoint": {"value": eq_value}, "gain": {"value": eq_gain}}

        return {
            "diyData": [
                _make_diy_dict(value, gain) for value, gain in zip(eq_values, eq_gains)
            ]
        }

    async def eq_custom(self, eq_gains: tuple[int, int, int, int, int, int]):
        # up to 6 ints in tenths of a dB (-30..30 = -3.0..+3.0 dB), for 62/250/1k/4k/8k/16k Hz
        return await self._command(
            CommandMessage(
                "soundEffect",
                {
                    "selectedIndex": int(
                        EqPreset.CUSTOMIZED
                    ),  # editing gains selects custom
                    "soundEffectDIY": self._eq_payload(eq_gains),
                },
            )
        )
