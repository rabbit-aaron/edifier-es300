"""Shared types for the edifier_es300 package."""

from dataclasses import dataclass
from typing import Any

type FrameData = dict[str, Any]  # a decoded JSON protocol frame


@dataclass
class Status:
    """Parsed device state from a `status_query` frame."""

    volume: int
    max_volume: int
    song: str | None
    lyric: str | None
    player_status: int
    input_source: FrameData
    light_effect: FrameData
    sound_index: int
    eq_selected_index: int
    eq_gains: list[int]
    battery: Any
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
            player_status=player["playerStatus"],
            input_source=frame["inputSource"],
            light_effect=frame["lightEffect"],
            sound_index=sound_effect["soundIndex"],
            eq_selected_index=sound_effect["selectedIndex"],
            eq_gains=[
                band["gain"]["value"]
                for band in sound_effect["soundEffectDIY"]["diyData"]
            ],
            battery=frame.get("battery"),
            raw=frame,
        )

    def __str__(self) -> str:
        return "\n".join(
            (
                "playing: %s / %s (status %s)"
                % (self.song or "-", self.lyric or "-", self.player_status),
                "volume : %s / %s" % (self.volume, self.max_volume),
                "source : %s" % (self.input_source,),
                "light  : %s" % (self.light_effect,),
                "eq     : soundIndex=%s selectedIndex=%s gains=%s"
                % (self.sound_index, self.eq_selected_index, self.eq_gains),
                "battery: %s" % (self.battery,),
            )
        )


type CommandResult = tuple[bool, Status | None]  # (acked, status)
