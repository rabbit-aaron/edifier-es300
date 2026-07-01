#!/usr/bin/env python3
"""Command-line interface for the Edifier ES300, built with click.

Run as a module:  python -m edifier_es300 [OPTIONS] COMMAND [ARGS]
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import NamedTuple

import click

from . import ES300, EqPreset, LightColor, LightEffect, Source, Status
from .typing import CommandResult

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


def _execute(target: Target, action: Action):
    """Open a short-lived connection and run one coroutine against the device."""

    async def runner():
        device = await _resolve(target)
        async with device:
            return await action(device)

    return asyncio.run(runner())


def _report(label: str, result: CommandResult) -> Status | None:
    acked, status = result
    click.echo(("OK " if acked else "?? ") + label)
    return status


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
    current = _execute(target, lambda device: device.status())
    click.echo(current or "no status (device idle; retry)")


@cli.command()
@click.argument("level", type=click.IntRange(0, 30))
@click.pass_obj
def vol(target: Target, level: int) -> None:
    """Set volume (0..30)."""
    current = _report(
        f"volume={level}", _execute(target, lambda device: device.volume(level))
    )
    click.echo("now %s" % (current.volume if current else "?"))


@cli.command()
@click.pass_obj
def play(target: Target) -> None:
    """Toggle play/pause."""
    _report("play", _execute(target, lambda device: device.play_pause()))


@cli.command()
@click.pass_obj
def pause(target: Target) -> None:
    """Toggle play/pause."""
    _report("pause", _execute(target, lambda device: device.play_pause()))


@cli.command()
@click.pass_obj
def next(target: Target) -> None:
    """Next track."""
    _report("next", _execute(target, lambda device: device.next_track()))


@cli.command()
@click.pass_obj
def prev(target: Target) -> None:
    """Previous track."""
    _report("prev", _execute(target, lambda device: device.prev_track()))


@cli.command()
@click.argument("level", type=click.IntRange(0, 100))
@click.pass_obj
def light_brightness(target: Target, level: int) -> None:
    """Set LED brightness (0..100)."""
    _report(
        f"brightness={level}", _execute(target, lambda device: device.brightness(level))
    )



@cli.command()
@click.argument("state", type=click.Choice(["on", "off"]))
@click.pass_obj
def light(target: Target, state: str) -> None:
    """Turn the LED strip on/off."""
    _report(
        f"light {state}",
        _execute(target, lambda device: device.light_switch(state == "on")),
    )


@cli.command()
@click.argument("name", type=click.Choice(["static", "breathing", "waterflow"]))
@click.pass_obj
def light_effect(target: Target, name: str) -> None:
    """Set light effect."""
    chosen = LightEffect[name.upper()]
    _report(f"effect {name}", _execute(target, lambda device: device.light_effect(chosen)))


@cli.command()
@click.argument("name", type=click.Choice(["yellow", "white"]))
@click.pass_obj
def light_color(target: Target, name: str) -> None:
    """Set light color (yellow or white)."""
    chosen = LightColor[name.upper()]
    _report(
        f"color {name}",
        _execute(target, lambda device: device.light_color(chosen)),
    )


@cli.command()
@click.argument("name", type=click.Choice(["bluetooth", "aux", "usb", "airplay"]))
@click.pass_obj
def source(target: Target, name: str) -> None:
    """Select input source."""
    chosen = Source[name.upper()]
    _report(
        f"source {name}", _execute(target, lambda device: device.input_source(chosen))
    )


@cli.command()
@click.argument(
    "name", type=click.Choice(["classic", "monitor", "game", "vocal", "customized"])
)
@click.pass_obj
def preset(target: Target, name: str) -> None:
    """Select an EQ preset."""
    chosen = EqPreset[name.upper()]
    current = _report(
        f"preset {name}", _execute(target, lambda device: device.eq_preset(chosen))
    )
    click.echo("selectedIndex %s" % (current.eq_selected_index if current else "?"))


@cli.command()
@click.argument("gains", type=int, nargs=-1)
@click.pass_obj
def eq(target: Target, gains: tuple[int, ...]) -> None:
    """Set custom 6-band gains in tenths of a dB (-30..30 = -3.0..+3.0 dB), for 62/250/1k/4k/8k/16k Hz."""
    label = "eq %s" % " ".join(str(gain) for gain in gains)
    current = _report(
        label, _execute(target, lambda device: device.eq_custom(list(gains)))
    )
    click.echo("gains %s" % (current.eq_gains if current else "?"))


if __name__ == "__main__":
    cli()
