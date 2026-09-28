# CLAUDE.md

This file provides guidance to Claude Code when working with code in this
repository.

## Overview

Gaia is a Python 3.11+ automation framework for greenhouses, terrariums and
aquariums. It drives sensors and actuators (GPIO / I2C / OneWire / WebSocket /
camera), runs control subroutines, and optionally reports to Ouranos through a
message broker.

Gaia runs unattended on a Raspberry Pi (down to a Pi Zero with 512 MB RAM) but
is developed on x86-64, where RPi-only libraries are never installed. Tests
therefore always run with `VIRTUALIZATION` enabled.

## Essential Commands

Always go through `uv`, bare `python` / `pytest` won't find the packages.

```bash
# Install everything (extras: camera/database/dispatcher; groups: test/qc)
uv sync --all-extras --all-groups
```

Run these **in order** after any change, fixing each before the next:

```bash
uv run ruff check .                                                     # 1. lint
uv run ty check src/                                                    # 2. type check
uv run pytest tests/ -v                                                 # 3. tests
uv run pytest tests/test_hardware.py::TestAddress -v                    # (scoped test if needed)
uv run coverage run -m pytest && uv run coverage report --show-missing  # 4. coverage
```

After changing `gaia-validators` locally, reinstall it into the venv
(`uv pip install -e <path-to-gaia-validators>`) before type checking or testing.

## High-Level Architecture

```
Engine (singleton)                 engine.py
└── Ecosystem[]                    ecosystem.py         - one per physical environment
    ├── Subroutines                subroutines/         - initialized in order: sensors → light → climate → weather → pictures → health
    ├── Hardware[]                 hardware/            - built from ecosystems.cfg, hot-reloadable
    ├── ActuatorHub                actuator_handler.py  - ActuatorHandlers + HystericalPIDs
    └── VirtualEcosystem           virtual.py           - physics simulation when virtualized

EngineConfig (singleton)           config/from_files.py - ecosystems.cfg + private.cfg, watched
└── EcosystemConfig[]              config/from_files.py - per-ecosystem view, one instance per ecosystem UID

Both Engine and Ecosystem can access their associated config object

Events                             events.py            - message-broker API with Ouranos
Database                           database/            - optional SQLAlchemy async
```

- `src/gaia/hardware/abc.py`: hardware base classes and mixins
- `src/gaia/subroutines/template.py`: `SubroutineTemplate`
- `tests/conftest.py`, `tests/subroutines/conftest.py`: test fixtures
- `tests/data.py`: test UIDs and hardware configs

Shared data types come from the companion package **`gaia_validators`**
(imported as `gv`); look there for `gv.*` definitions.

## Key Development Patterns

### Configuration

- The config layer (`EngineConfig`, `EcosystemConfig`) only reads, validates
  and saves. **Never send event payloads from it**, callers do (`on_crud` in
  `events.py`).
- CRUD resolves to `EcosystemConfig` methods, not `Ecosystem` ones. Keep
  `Ecosystem`-level config methods as thin delegates, or CRUD will bypass them.
- `EcosystemConfig` mutations stay in memory until `save()`.

### Hardware

- Build a hardware class from exactly one address mixin plus one or more type
  mixins (enforced by `Hardware.__init_subclass__`).
- Vendor/compat driver classes take a `Device` suffix and live in the
  package's `_devices/` directory.
- Call `ActuatorHandler.reset_cached_actuators()` after mounting/unmounting hardware.
- Keep `ActuatorHandler` unaware of virtualization; virtual hardware pushes
  their state into `VirtualEcosystem` itself.

### Actuators

- Balance every `ActuatorHandler.activate()` with a `deactivate()`.
  Handlers are reference-counted and shared between subroutines.
- Wrap multi-step state changes in `ActuatorHandler.update_status_transaction()`.
- Keep constructors free of side effects; handlers and PIDs are created
  through `ActuatorHub.get_handler()` / `get_pid()`.

### Subroutines

- Each subroutine has its own execution model (Sensors: interval job,
  Climate: triggered by Sensors, Light: own loop, Weather: cron jobs). Don't
  merge them into a common loop.
- Light is schedule-driven and must work without sensors, don't fold it
  into or derive it from Climate.
- Always check data properties (`sensors_data`, `plants_health`, ...) for
  `gv.Empty` before use.
- Set `_started` only in the `try ... else` branch of `start()` / `stop()`, so it
  reflects actual state.

### Optional dependencies & memory

Memory, not CPU, is the constraint on the Pi.

- Access optional extras (`cv2`, `np`, `SerializableImage`, `sqlalchemy`,
  dispatchers) through `gaia.dependencies`: a `TYPE_CHECKING` import for
  annotations, a function-local import at runtime.
- **Never import `cv2`, `numpy` or `gaia_validators.image` at module level.**
- Degrade gracefully when an extra is missing (`check_dependencies()`).

### Error handling

- Prefer degraded operation over crashing: a failing subroutine or plugin
  is logged and skipped. When reviewing, check the failure is logged
  clearly rather than flagging the swallow.
- Use `assert` for internal contracts; raise only at public API boundaries.
- Never `commit()` inside database helpers, the caller owns the
  transaction.

### Type checking

- To suppress a `ty` error, explain it on the line above with
  `# Valid ignore: <reason>`, then add `# ty: ignore[rule]` inline.
- RPi-only imports: under `TYPE_CHECKING`, import the `_compatibility` stubs
  unconditionally; the runtime `adafruit_*` imports carry
  `# ty: ignore[unresolved-import]`.
- Use `**kwargs: Unpack[FooDict]` for TypedDict kwargs.

### Style

- Async-first, type hints everywhere, `TYPE_CHECKING` imports to break
  cycles.
- Separate sections with a `# ---...---` / `#   Name` / `# ---...---` banner
  (see any module in `src/gaia/`).
- In `gaia_validators`, define each `FooDict` TypedDict **before** its
  `class Foo(BaseModel[FooDict])`.

## Testing

- Mark async test classes with `@pytest.mark.asyncio` (strict mode).
- Use the `engine` / `ecosystem` fixtures rather than building singletons by
  hand; they detach `Engine` / `EngineConfig` between tests.
- Don't hold `EcosystemConfig` references across tests, stale instances get
  reused.
- Use `await yield_control()` (`tests/utils.py`) to let background tasks run.
- Renew any asyncio primitive held by a session-scoped fixture between tests,
  each test gets a new event loop. Suspect this first when a background
  task silently never runs.
- Assert on logs with `caplog.text`.
- `"default"` in a test hardware address is a placeholder that will be switched 
  to a valid address at runtime, not an error.
- Always `await manager.stop()` on a `WebSocketHardwareManager` in tests, or
  the test hangs.

## Changelog & Versioning

- Keep `CHANGELOG.md`'s `## Unreleased` up to date (Keep-a-Changelog,
  sections `Added` / `Changed` / `Removed` / `Fixed` / `Security` /
  `Development`). Group entries by theme and end each with its PR numbers,
  e.g. `(#478)`. Tooling, tests and CI go under `Development`.
- Check the changelog before any version bump.
- The app version is independent of Ouranos. Compatibility is carried by
  `GAIA_CONTRACT`: bump it only for a breaking change to dispatcher events or
  payloads, together with ouranos-core.

## Critical Notes

- **Do NOT** modify `migrations/` (Alembic → human review required).
- **Do NOT** modify `.github/workflows/`.
- **Do NOT** run `scripts/install.sh` or `scripts/update.sh` against the real
  machine. Use `scripts/utils/sandbox.sh`. `tests/test_scripts.py` guards
  their version strings.
- **Do NOT** simplify `scripts/utils/ring_log.sh` or how `start.sh` launches
  it; the comments there explain why.
