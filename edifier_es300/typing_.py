"""Shared types for the edifier_es300 package."""

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any

type FrameData = dict[str, Any]  # a decoded JSON protocol frame


class Source(IntEnum):
    """Input sources; value is inputSource.selectedIndex (all verified live)."""

    BLUETOOTH = 0
    AUX = 1
    USB = 2
    AIRPLAY = 3


class EqPreset(IntEnum):
    """EQ presets; value is soundEffect.selectedIndex (CUSTOMIZED is the custom slot)."""

    CLASSIC = 0
    MONITOR = 1
    GAME = 2
    VOCAL = 3
    CUSTOMIZED = 4


class LightColor(Enum):
    """Ambient LED colors; value is the RGB the app sends (ES300 only does these two)."""

    YELLOW = {"r": 255, "g": 170, "b": 60}
    WHITE = {"r": 255, "g": 255, "b": 255}


class LightEffect(IntEnum):
    """Ambient LED effects; value is lightEffect.selectedIndex."""

    STATIC = 1
    BREATHING = 2
    WATERFLOW = 3


class BatteryStatus(IntEnum):
    """Battery power state; value is battery.status."""

    CONNECTED = 1  # external power connected
    DISCONNECTED = 2  # running on battery


class PlayerStatus(IntEnum):
    """Playback state; value is player.playerStatus."""

    STOPPED = 0
    PLAYING = 1


@dataclass
class Status:
    """Parsed device state from a `status_query` frame."""

    volume: int
    max_volume: int
    song: str | None
    lyric: str | None
    player_status: PlayerStatus
    input_source: FrameData
    light_effect: FrameData
    sound_index: int
    eq_selected_index: int
    eq_gains: list[int]
    battery: Any
    timer_shutdown: (
        FrameData | None
    )  # {timerIndex, timeShutdown(min), timeRemaining(min)}
    raw: FrameData  # the original frame, for fields not surfaced above

    @classmethod
    def from_frame(cls, frame: FrameData) -> "Status":
        player = frame["player"]
        sound_effect = frame["soundEffect"]
        return cls(
            volume=player["volume"],
            max_volume=player["maxVolume"],
            song=player.get("song"),
            lyric=player.get("lyric"),
            player_status=PlayerStatus(player["playerStatus"]),
            input_source=frame["inputSource"],
            light_effect=frame["lightEffect"],
            sound_index=sound_effect["soundIndex"],
            eq_selected_index=sound_effect["selectedIndex"],
            eq_gains=[
                band["gain"]["value"]
                for band in sound_effect["soundEffectDIY"]["diyData"]
            ],
            battery=frame.get("battery"),
            timer_shutdown=frame.get("timerShutdown"),
            raw=frame,
        )

    def _timer_line(self) -> str:
        # The device's timeRemaining doesn't count down (it holds the armed value
        # until shutdown), so only the configured duration is worth showing.
        minutes = (self.timer_shutdown or {}).get("timeShutdown", 0)
        return "%smin" % minutes if minutes else "off"

    def __str__(self) -> str:
        return "\n".join(
            (
                "playing: %s / %s (status %r)"
                % (self.song or "-", self.lyric or "-", self.player_status),
                "volume : %s / %s" % (self.volume, self.max_volume),
                "source : %r" % Source(self.input_source["selectedIndex"]),
                "effect : %r" % LightEffect(self.light_effect["selectedIndex"]),
                "color  : %r" % LightColor(self.light_effect["color"]),
                "eq     : %r gains=%s"
                % (EqPreset(self.eq_selected_index), self.eq_gains),
                "battery: %s%% (%r)"
                % (self.battery["box"], BatteryStatus(self.battery["status"])),
                "timer  : %s" % self._timer_line(),
            )
        )


type CommandResult = tuple[bool, Status | None]  # (acked, status)
