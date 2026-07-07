import asyncio
from collections.abc import Awaitable, Callable
from typing import NamedTuple

import click

from edifier_es300 import ES300, EqPreset, LightColor, LightEffect, Source

DEFAULT_PORT: int = 8080

type Action = Callable[[ES300], Awaitable]


class Target(NamedTuple):
    """Where to reach the speaker; host None means auto-discover on the LAN."""

    host: str | None
    port: int


async def _resolve(target: Target) -> ES300:
    """Return the target speaker: the given host, or the first one discovered."""
    if target.host:
        return ES300(target.host, target.port)
    speakers = await ES300.discover()
    if not speakers:
        raise click.ClickException("no ES300 speakers found on the network")
    return speakers[0]


def _run_command(target: Target, action: Action):
    """Open a short-lived connection and run one coroutine against the device."""

    async def _runner():
        speaker = await _resolve(target)
        async with speaker:
            return await action(speaker)

    return asyncio.run(_runner())


@click.group()
@click.option(
    "--host", default=None, help="Speaker IP (default: auto-discover on the LAN)."
)
@click.option(
    "--port", default=DEFAULT_PORT, show_default=True, help="Control-channel TCP port."
)
@click.pass_context
def cli(ctx: click.Context, host: str | None, port: int) -> None:
    """Control an Edifier ES300 speaker over Wi-Fi."""
    ctx.obj = Target(host, port)


@cli.command()
def discover() -> None:
    """List ES300 speakers found on the local network."""
    speakers = asyncio.run(ES300.discover())
    if not speakers:
        click.echo("no ES300 speakers found (same Wi-Fi as the speaker?)")
        return
    for speaker in speakers:
        click.echo(speaker)


@cli.command()
@click.pass_obj
def status(target: Target) -> None:
    """Dump volume / source / light / EQ / battery."""
    current = _run_command(target, lambda speaker: speaker.status())
    click.echo(current or "no status (device idle; retry)")


@cli.command()
@click.argument("level", type=click.IntRange(0, 30))
@click.pass_obj
def volume(target: Target, level: int) -> None:
    """Set volume (0..30)."""
    _run_command(target, lambda speaker: speaker.volume(level))


@cli.command()
@click.pass_obj
def play(target: Target) -> None:
    """Resume playback."""
    _run_command(target, lambda speaker: speaker.play())


@cli.command()
@click.pass_obj
def pause(target: Target) -> None:
    """Pause playback."""
    _run_command(target, lambda speaker: speaker.pause())


@cli.command()
@click.pass_obj
def play_pause(target: Target) -> None:
    """Toggle play/pause."""
    _run_command(target, lambda speaker: speaker.play_pause_toggle())


@cli.command()
@click.pass_obj
def next_track(target: Target) -> None:
    """Next track."""
    _run_command(target, lambda speaker: speaker.next_track())


@cli.command()
@click.pass_obj
def previous_track(target: Target) -> None:
    """Previous track."""
    _run_command(target, lambda speaker: speaker.previous_track())


@cli.command()
@click.pass_obj
def shutdown(target: Target) -> None:
    """Power the speaker off (no remote power-on; use the physical button)."""
    _run_command(target, lambda speaker: speaker.shutdown())


@cli.command()
@click.argument("minutes", type=click.IntRange(0, 1440))
@click.pass_obj
def timer_shutdown(target: Target, minutes: int) -> None:
    """Set sleep timer in minutes (0 = off; app presets 5/15/30/60/180)."""
    _run_command(target, lambda speaker: speaker.timer_shutdown(minutes))


@cli.command()
@click.argument("level", type=click.IntRange(0, 100))
@click.pass_obj
def light_brightness(target: Target, level: int) -> None:
    """Set LED brightness (0..100)."""
    _run_command(target, lambda speaker: speaker.brightness(level))


@cli.command()
@click.argument("state", type=click.Choice(["on", "off"]))
@click.pass_obj
def light(target: Target, state: str) -> None:
    """Turn the LED strip on/off."""
    _run_command(target, lambda speaker: speaker.light_switch(state == "on"))


@cli.command()
@click.argument("name", type=click.Choice(["static", "breathing", "waterflow"]))
@click.pass_obj
def light_effect(target: Target, name: str) -> None:
    """Set light effect."""
    chosen = LightEffect[name.upper()]
    _run_command(target, lambda speaker: speaker.light_effect(chosen))


@cli.command()
@click.argument("name", type=click.Choice(["yellow", "white"]))
@click.pass_obj
def light_color(target: Target, name: str) -> None:
    """Set light color (yellow or white)."""
    chosen = LightColor[name.upper()]
    _run_command(target, lambda speaker: speaker.light_color(chosen))


@cli.command()
@click.argument("name", type=click.Choice(["bluetooth", "aux", "usb", "airplay"]))
@click.pass_obj
def source(target: Target, name: str) -> None:
    """Select input source."""
    chosen = Source[name.upper()]
    _run_command(target, lambda speaker: speaker.input_source(chosen))


@cli.command()
@click.argument(
    "name", type=click.Choice(["classic", "monitor", "game", "vocal", "customized"])
)
@click.pass_obj
def eq_preset(target: Target, name: str) -> None:
    """Select an EQ preset."""
    chosen = EqPreset[name.upper()]
    _run_command(target, lambda speaker: speaker.eq_preset(chosen))


@cli.command()
@click.argument("gains", type=int, nargs=6)
@click.pass_obj
def eq(target: Target, gains: tuple[int, int, int, int, int, int]) -> None:
    """Set custom 6-band gains in tenths of a dB (-30..30 = -3.0..+3.0 dB), for 62/250/1k/4k/8k/16k Hz."""
    _run_command(target, lambda speaker: speaker.eq_custom(gains))


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)
    cli()
