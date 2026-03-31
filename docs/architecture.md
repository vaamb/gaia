# Gaia's architecture

Gaia is organised as a hierarchy of singletons and managed objects. Each level
owns the one below it and exposes a weakref upward.

## Overview

```
┌───────────────────────────────────────────────────────────────────┐
│  Engine  (singleton)                                              │
│  ├── EngineConfig  (singleton)                                    │
│  ├── Events  (optional — Ouranos dispatcher)                      │
│  │                                                                │
│  └── Ecosystem  (one per configured ecosystem)                    │
│      ├── EcosystemConfig                                          │
│      ├── ActuatorHub                                              │
│      ├── VirtualEcosystem  (optional — simulation mode)           │
│      │                                                            │
│      └── Subroutines  (started/stopped per ecosystem config)      │
│          ├── Sensors   ──► Hardware (sensors)                     │
│          ├── Light     ──► ActuatorHub  ──► Hardware (actuators)  │
│          ├── Climate   ──► ActuatorHub  ──► Hardware (actuators)  │
│          ├── Weather   ──► ActuatorHub  ──► Hardware (actuators)  │
│          ├── Pictures  ──► Hardware (cameras)                     │
│          └── Health                                               │
└───────────────────────────────────────────────────────────────────┘
```

## Components

### Engine

`Engine` is a singleton (via `SingletonMeta`) that owns all `Ecosystem`
instances. It is responsible for:

- Loading `EngineConfig` and watching config files for changes
- Starting and stopping ecosystems based on configuration
- Optionally connecting to Ouranos via an `AsyncDispatcher` (AMQP or Redis),
  which is exposed to ecosystems through `Engine.event_handler`
- Reacting to config changes at runtime via `Engine._loop`, which waits on
  `EngineConfig.new_config` (an `asyncio.Condition`) and updates ecosystems when it fires

### Config (EngineConfig and EcosystemConfig)

```
                    ┌─────────────────────────────┐
  private.cfg ─────►│                             │
                    │  EngineConfig  (singleton)  │
ecosystems.cfg ────►│                             │
                    └──────────────┬──────────────┘
                                   │ read-through (by uid)
                      ┌────────────┴────────────┐
                      │                         │
              EcosystemConfig(A)        EcosystemConfig(B)
              (convenience view)        (convenience view)
```

`EngineConfig` is a singleton that parses both `private.cfg` and
`ecosystems.cfg` and provides all configuration data: application-level
settings (paths, connection parameters, feature flags) as well as all
per-ecosystem parameters (lighting schedule, climate targets, hardware
definitions, actuator groups). It watches both files and reparses on changes,
then sets `EngineConfig.new_config` to notify `Engine._loop`.

`EcosystemConfig` is a uid-based singleton (one instance per ecosystem uid)
that acts as a convenience layer over `EngineConfig`, exposing only the
configuration relevant to a given ecosystem.

The full config-change round-trip:

```
ecosystems.cfg changes
        │
        ▼
  EngineConfig reparses
        │
        ▼
  new_config (Condition) fires
        │
        ▼
  Engine._loop wakes
        │
        ▼
  Ecosystems updated
```

### Ecosystem

One `Ecosystem` instance is created per ecosystem defined in `ecosystems.cfg`.
It:

- Instantiates and manages all subroutines
- Owns an `ActuatorHub` that dispatches commands to the appropriate actuators
- Optionally owns a `VirtualEcosystem` when `VIRTUALIZATION` is enabled in
  `GaiaConfig`, allowing subroutines to run in simulation without physical
  hardware

Ecosystems are stored in `Engine.ecosystems` (dict). Each ecosystem holds a
weakref to its engine via `Ecosystem.engine`.

### Subroutines

Subroutines are the active units of Gaia. They run as async loops and interact
with the physical world through `Hardware` instances. They are started and
stopped individually based on the management flags in `ecosystems.cfg`.

Subroutines start in this order and stop in reverse:

| Order | Subroutine  | Role                                          |
|------:|-------------|-----------------------------------------------|
|     1 | `Sensors`   | Reads sensor data periodically                |
|     2 | `Light`     | Manages lighting schedule and actuators       |
|     3 | `Climate`   | PID-based temperature and humidity control    |
|     4 | `Weather`   | Punctual cron-style climate actuator commands |
|     5 | `Pictures`  | Periodic camera captures                      |
|     6 | `Health`    | Plant health metrics from images              |

Subroutines are stored in `Ecosystem.subroutines` (dict). Each subroutine
holds a weakref to its ecosystem via `Subroutine.ecosystem`.

### Hardware

`Hardware` instances are created from the hardware definitions in
`ecosystems.cfg` and managed by subroutines. They are composed from two
orthogonal sets of mixins:

- **Address mixin** — defines the physical protocol:
  `gpioAddressMixin`, `i2cAddressMixin`, `OneWireAddressMixin`,
  `WebSocketAddressMixin`
- **Type mixin** — defines the hardware role:
  `SensorMixin`, `ActuatorMixin` (+ `SwitchMixin`, `DimmerMixin`),
  `CameraMixin`

`Hardware.__init_subclass__` enforces that every concrete class includes
exactly one address mixin and at least one type mixin, raising `TypeError` at
class-definition time otherwise.

Hardware instances are looked up by model name (the class name) from a central
registry. See [adding_hardware.md](adding_hardware.md) for how to add new
models.

### ActuatorHub

`ActuatorHub` sits between the subroutines and the actuator `Hardware`
instances. It groups actuators by function (e.g. all heaters, all fans) and
routes commands to the right group. Both `Climate` (long-term PID control) and
`Weather` (punctual timed commands) interact with hardware through it.

### VirtualEcosystem

When `VIRTUALIZATION` is enabled, each `Ecosystem` owns a `VirtualEcosystem`
that simulates the physical environment. Virtual actuators interact with it
directly, allowing the full subroutine stack — sensors, light, climate, PID —
to run without any real hardware. Used for testing and development.
