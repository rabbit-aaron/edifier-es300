# edifier_es300

Control an **Edifier ES300** speaker over Wi-Fi, without the Edifier Home app.
Ships an `asyncio` library and a `click`-based command-line interface.

- **Library** (`edifier_es300`): stdlib-only, fully async.
- **CLI** (`python -m edifier_es300`): thin wrapper over the library; needs `click`.

## Requirements

- Python 3.13+
- `click` (CLI only — the library has no third-party dependencies)

```bash
uv sync        # or: pip install click
```

## Library usage

Everything is async. The `ES300` connection is an async context manager — reuse a
single connection for a burst of commands (the device drops an idle socket after a
few seconds).

```python
import asyncio
from edifier_es300 import ES300, Source, EqPreset, LightEffect, LightColor


async def main():
  async with ES300("192.168.1.123", 8080) as device:
    print(await device.status())  # parsed Status (see below)

    await device.volume(20)  # 0..30
    await device.play()  # resume
    await device.pause()  # pause
    await device.play_pause_toggle()  # toggle
    await device.next_track()
    await device.previous_track()

    await device.input_source(Source.AIRPLAY)

    await device.light_switch(True)
    await device.brightness(60)  # 0..100
    await device.light_effect(LightEffect.BREATHING)
    await device.light_color(LightColor.YELLOW)

    await device.eq_preset(EqPreset.VOCAL)
    await device.eq_custom([10, 5, 0, 0, 0, -5])  # tenths of a dB (-30..30)


asyncio.run(main())
```

### Discovery

`ES300.discover()` broadcasts on the LAN and returns a list of ready-to-use
`ES300` objects (host, port, and name filled in):

```python
speakers = await ES300.discover(seconds=3.0)
for speaker in speakers:
    print(speaker)            # "EDIFIER ES300  192.168.1.123:8080"

async with speakers[0] as device:
    await device.volume(15)
```

### Return values

- Command methods (`volume`, `play`, `input_source`, `eq_custom`, …) return
  `CommandResult`, a `tuple[bool, Status | None]` of `(acknowledged, new_state)`.
- `status()` returns a `Status | None` (`None` only if the device stays silent).

### `Status`

`str(status)` renders a human-readable dump (this is what the CLI `status` prints):

```
playing: - / - (status 0)
volume : 6 / 30
source : Source.AIRPLAY
effect : LightEffect.STATIC
color  : LightColor.YELLOW
eq     : EqPreset.CLASSIC gains=[0, 0, 0, 0, 0, 0]
battery: 100% (BatteryStatus.CONNECTED)
```

Fields: `volume`, `max_volume`, `song`, `lyric`, `player_status`, `input_source`,
`light_effect`, `sound_index`, `eq_selected_index`, `eq_gains`, `battery`, and
`raw` (the full status frame for anything not surfaced).

### Enums

| Enum | Values | Notes |
|------|--------|-------|
| `Source` | `BLUETOOTH=0`, `AUX=1`, `USB=2`, `AIRPLAY=3` | input source |
| `EqPreset` | `CLASSIC=0`, `MONITOR=1`, `GAME=2`, `VOCAL=3`, `CUSTOMIZED=4` | `CUSTOMIZED` is the editable slot |
| `LightEffect` | `STATIC=1`, `BREATHING=2`, `WATERFLOW=3` | ambient LED effect |
| `LightColor` | `YELLOW`, `WHITE` | value is the RGB dict; hardware only does these two |
| `BatteryStatus` | `CONNECTED=1`, `DISCONNECTED=2` | external power state (read-only) |

`Source`, `EqPreset`, and `LightEffect` are `IntEnum`s, so methods also accept a
plain `int`. Setting `eq_custom` gains automatically selects `EqPreset.CUSTOMIZED`.

## CLI usage

```bash
python -m edifier_es300 [--host IP] [--port N] COMMAND [ARGS]
```

- `--host` — speaker IP. Omit it to **auto-discover** the first speaker on the LAN.
- `--port` — control-channel TCP port (default `8080`).

| Command | Args | Description |
|---------|------|-------------|
| `discover` | — | list speakers on the LAN (`name  ip:port`) |
| `status` | — | dump volume / source / light / EQ / battery |
| `vol` | `LEVEL` (0..30) | set volume |
| `play` / `pause` | — | resume / pause playback |
| `play-pause` | — | toggle play/pause |
| `next` / `prev` | — | skip track |
| `light` | `on` \| `off` | LED strip on/off |
| `light-brightness` | `LEVEL` (0..100) | LED brightness |
| `light-effect` | `static` \| `breathing` \| `waterflow` | LED effect |
| `light-color` | `yellow` \| `white` | LED color |
| `source` | `bluetooth` \| `aux` \| `usb` \| `airplay` | input source |
| `preset` | `classic` \| `monitor` \| `game` \| `vocal` \| `customized` | EQ preset |
| `eq` | `GAINS...` | 6 custom gains, tenths of a dB (-30..30 = -3.0..+3.0 dB) |

### Examples

```bash
python -m edifier_es300 discover
python -m edifier_es300 status                       # auto-discover, then dump state
python -m edifier_es300 --host 192.168.1.123 vol 22
python -m edifier_es300 source airplay
python -m edifier_es300 light-effect breathing
python -m edifier_es300 light-color yellow
python -m edifier_es300 preset vocal
python -m edifier_es300 eq -- 10 5 0 0 0 -5          # use -- so negatives aren't read as options
```

> Note: negative EQ gains look like CLI options, so prefix the gain list with `--`.
