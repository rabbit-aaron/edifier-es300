# CLAUDE.md — working in `edifier_es300`

Guidance for working in this Python package. Read `README.md` for user-facing usage.
This file is about the *code*: how it's organized, the conventions to follow, and the
Python-specific traps hit while building it.

## What this is

An `asyncio` package to control an Edifier ES300 speaker on the local network, plus a
`click` CLI. The library is stdlib-only; only the CLI needs `click`. Python 3.13+.

## Module layout

- `__init__.py` — the `ES300` async client: connection lifecycle (async context
  manager), the request/response plumbing, the public command methods (volume,
  transport, light, EQ, input source), and the `discover()` classmethod. Stdlib-only.
- `typing.py` — **all shared types**: `FrameData`, the `Status` dataclass,
  `CommandResult`, and every device enum (`Source`, `EqPreset`, `LightEffect`,
  `LightColor`, `BatteryStatus`, `PlayerStatus`). It imports nothing from
  the package, so it can never be part of an import cycle.
- `__main__.py` — the `click` CLI (`python -m edifier_es300`). A thin wrapper:
  resolve a target (explicit `--host/--port` or auto-discover), open one connection
  per command, format output with `click.echo`.
- `discovery.py` — network discovery of speakers on the LAN; returns
  `DiscoveredDevice`s. Uses `logging`, not `print`.

## Conventions

- **Types live in `typing.py`.** If a type is used by more than one module — or would
  cause a circular import if defined in `__init__` — define it there. `__init__`
  re-exports the ones it uses so `from edifier_es300 import Source` keeps working.
  (Enums not referenced by `__init__`, like `BatteryStatus`, are imported from
  `edifier_es300.typing` to avoid an unused import.)
- **Type aliases use the `type` keyword** (PEP 695): `type FrameData = dict[str, Any]`.
- **Name collisions get a trailing underscore.** Prefer a descriptive name first
  (this is why the JSON-dict alias is `FrameData`, not `json_`); fall back to a `_`
  suffix only for a genuine clash with a stdlib module or builtin.
- **No 1–2 character variable names.** Spell them out (`header_pos`, `payload_len`,
  `device`, `frame`, `chunk`).
- **Device settings are enums.** `IntEnum` for index-valued settings (`Source`,
  `EqPreset`, `LightEffect`, `BatteryStatus`, `PlayerStatus`); a plain `Enum` for `LightColor` (its
  value is the RGB payload). For display, format members with `repr` (`%r` / `{x!r}`),
  which gives `<Source.AIRPLAY: 3>`; plain `str` on an `IntEnum` is just the number.
  Index enums also accept a raw `int` in method signatures (`Source | int`).
- **CLI:** one `click` command per action; enum args use `click.Choice(...)` resolved
  via `Enum[name.upper()]`. Omitting `--host` triggers auto-discovery.
- **Logging, not print,** in library/discovery code. Only the CLI emits output
  (through `click.echo`).

## Python gotchas hit here

- **`IntEnum.__str__` returns the number** in 3.11+ (`str(Source.AIRPLAY)` → `3`, not
  `Source.AIRPLAY`). Use `repr` for display (`%r`) — `repr(Source.AIRPLAY)` →
  `<Source.AIRPLAY: 3>` — which is why `Status.__str__` formats enum fields with `%r`.
- **An `IntEnum` member with value `0` is falsy** (`EqPreset.CLASSIC`). Never test an
  enum lookup with truthiness — use `is None`.
- **`LightColor`'s value is a dict** (unhashable). `LightColor(some_dict)` still works
  because `Enum.__new__` falls back to a linear value search, but the hashed
  value→member map isn't built for it.
- **PEP 695 `type` aliases are lazy** — the right-hand side isn't evaluated at import.
  That's what lets `typing.py` hold `CommandResult = tuple[bool, Status | None]` and be
  imported by `__init__` without an import cycle.

## Testing

- **Verify against the real speaker.** This project favors empirical checks over specs:
  after a change, run the CLI live — typically `status`, then the command you changed,
  then `status` again to confirm the effect landed.
- Use `uv` + the project venv for tooling. The library itself needs no dependencies.

## Running

The package modules sit at the top of this directory, so run from the directory that
*contains* `edifier_es300/`:

- CLI: `python -m edifier_es300 <command>`
- Library: `from edifier_es300 import ES300`

The interpreter needs `click` importable for the CLI.
