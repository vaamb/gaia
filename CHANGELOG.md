# Changelog

## Unreleased

### Hardware — major refactor

- **Mixin-based composition** replaces the old class
  hierarchy — address mixins (`gpioAddressMixin`, `i2cAddressMixin`,
  `OneWireAddressMixin`, `WebSocketAddressMixin`, `PiCameraAddressMixin`) and type
  mixins (`SensorMixin`, `ActuatorMixin`, `SwitchMixin`, `DimmerMixin`, `CameraMixin`)
  are the two required composition axes; `Hardware.__init_subclass__` enforces these
  constraints at class-definition time, raising `TypeError` if violated; `__slots__`
  removed
- `Hardware`'s dependency on `Ecosystem` removed — hardware no longer holds a reference
  upward to its ecosystem
- Hardware reorganised into dedicated sub-modules: camera (`hardware/camera/`),
  multiplexer (`hardware/multiplexer/`); compatibility devices renamed with a `Device`
  suffix to avoid confusion with `Hardware` subclasses
- `Hardware._on_initialize()` / `_on_terminate()` hooks added for lifecycle extension;
  instances renamed from `create/shutdown` to `initialize/terminate`
- Secondary hardware address removed — hardware now has a single address only
- New `Hardware` API: `Switch.get_status()`, `Dimmer.get_pwm_level()`;
  `turn_on()` / `turn_off()` and `set_pwm_level()` now return a boolean indicating
  success; `WebSocketHardware._execute_action()` can return a data payload from the
  device
- Hardware can be **inactivated** without being removed from config
- Live hardware reload: `Ecosystem.refresh_hardware()` remounts any hardware whose
  config has changed without requiring a full restart
- `Hardware.groups` added to allow a single instance to belong to multiple actuator
  groups; `Ecosystem.get_hardware_group_uids()` added to query group membership

### Simulation (VirtualEcosystem)

- `virtualActuator` subclasses push state directly into `VirtualEcosystem`, enabling
  the full subroutine stack to run in simulation without real hardware; `VirtualDevice`
  uses `ecosystem_uid` instead of an `Ecosystem` instance

### Engine

- `EngineConfig._get_dir()` promoted to `GaiaConfig.get_path()` with a `_paths` cache;
  config watchdog extracted to `ConfigWatchdog`, checksum tracking to `ChecksumTracker`
- `Engine` lifecycle tracked by `EngineState` enum
  (`INITIALIZED → RUNNING → PAUSED → STOPPED → TERMINATED`)

### Subroutines and sensors

- `SensorRead` named tuple introduced as the output type of `BaseSensor.get_data()`;
  `Health{Buffer,Record}` merged into `Sensor{Buffer,Record}`; `Ecosystem.subroutines`
  made private

### Actuator control

- Weather subroutine added for punctual, cron-style actuator commands; event renamed
  from `weather_parameter` to `weather_event`
- Actuators managed by named *groups* rather than hardware type; light subroutine
  accepts a custom actuator group via config
- `ActuatorHandler` and `HystericalPID` storage converted to lazy-initialised weak
  references; registration moved from constructors to factory methods on `ActuatorHub`
  (`get_pid()` / `get_handler()`)
- `Climate` subroutine now allows choosing which sensor measures drive each climate
  parameter; `EcosystemConfig.update_{...}` methods support **partial updates**

### New hardware models

- DS18B20 (1-Wire temperature sensor) added
- WebSocket hardware for remote actuators and sensors added

### CLI

- `gaia config create` — generate default config files
- `gaia config check` — validate configuration files

### Database

- Alembic added for database schema migrations

### Other

- Production logging switched to a less verbose format
- Config file dump is more compact: climate, hardware, and plants sections shortened
- `EcosystemConfig.lighting_hours` setter removed — set via config files only

### Tooling

- `uv` replaces the previous virtual environment manager
- `ty` (Astral) type checking added to the GitHub Actions workflow

---

## 0.10.0 — 2025-08-29

- `Events.validate_payload()` converted to a decorator (`@validate_payload`) for 
  cleaner event handler definitions
- "plants" event added to communicate plant health data to Ouranos
- Deprecated "environmental_parameters" event removed (superseded by the split 
  events from 0.9.0)
- `Hardware` instances no longer materialised during config validation
- `hardware.Address` error messages improved for invalid address formats
- `gaia logs` now uses `gaia.log` as its source; `gaia stdout` restored
- `start.sh` gains a `--foreground` option for use with systemd
- Installation and update scripts improved

---

## 0.9.0 — 2025-07-02

- "nycthemeral_cycle" and "light_data" events merged into a single
  "nycthemeral_info" event
- "environmental_parameters" event split into three focused events for finer granularity
- `ActuatorHandler.turn_to()` uses an async timer with callback when a countdown 
  is set
- Buffered data exchange made more resilient: subroutines buffer data even when the
  sending task times out
- Actuator status is now correctly updated when transitioning from "manual" to
  "automatic" mode
- Fix: "actuators_data" event was dispatched to the wrong dispatcher
- Fix: `ActuatorHandler`'s `Timer` was reset before finishing its task
- `HystericalPID.update_pid()` now accepts `None` as input
- Python 3.13 added to the test matrix

---

## 0.8.0 — 2024-11-28

- `Pictures` subroutine added (periodic camera captures)
- `Camera` hardware added; lazy device initialisation in `Camera` and `BaseSensor`
- Camera images posted to Ouranos' aggregator file server instead of being sent
  through the message broker (avoids saturating the broker with large payloads);
  pictures can be resized and compressed before sending
- Fix: `Picamera2` could be initialised twice; Camera usability on raspi0 improved
- `Pictures` subroutine no longer requires `scikit-image`; OpenCV replaces it for 
  image processing
- `Health` subroutine rewritten to perform real image analysis using spectral vegetation
  indices (MPRI, NDRGI, NDVI, VARI) instead of producing random mock data
- Multiplexer now instantiated once per address and cached at the `Hardware` level
  instead of being recreated on each I2C access; `Hardware` validates that multiplexed
  addresses are only used with I2C multiplexers; `@` used as separator between
  multiplexer and device address; `&` used to separate primary and secondary hardware
  addresses
- `virtualHardware` falls back to compatibility devices when no `VirtualEcosystem` is
  reachable; hardware models are automatically promoted to their `virtual{...}` 
  equivalent when virtualization is enabled
- PID is now reset when `ActuatorHandler` mode changes
- Fix: `Light.compute_level()` set to `500_000.0` when no dimmable lights are available,
  preventing flickering
- Actuator data logged with UTC-based timestamps; `ActuatorHandler.as_record()` now
  requires a timestamp
- Ping is sent indefinitely once connected to the message broker
- Python 3.9 and 3.10 support dropped
- `ruff` added to the CI pipeline
- In-memory SQLite database used during tests; coverage added to the CI pipeline

---

## 0.7.1 — 2024-07-31

- `ActuatorHandler`s now properly activated before transactions, fixing a regression
  introduced in 0.7.0
- Gaia–Ouranos initial handshake improved: ecosystem data is now fully acknowledged 
  before background jobs are scheduled; retry mechanism added for missed initialisation
  data
- Actuator state changes logged

---

## 0.7.0 — 2024-06-23

- **Gaia is now fully async** (`asyncio`/`async`/`await` throughout; `gevent` and
  `ThreadPoolExecutor` removed)
- Sensor alarms added (creation, management, and event dispatching)
- Sun times computed locally instead of downloaded from an external service
- `Sensors.send_data`, `Light.routine()`, and `Events.ping()` run as async `Task`s,
  decoupling upload from acquisition
- `Climate` subroutine triggered by `Sensors.routine` to always operate on fresh data
- `EcosystemConfig.lighting_hours` used as the single source of truth for lighting
  computations
- Sensor data automatically sent to Ouranos after each update
- Ping event now includes the list of ecosystem UIDs and their status
- Data logged to disk in TSV format instead of JSON
- `update_ecosystem` CRUD event added
- Measure info now includes units
- ENS160 virtual hardware added
- Ecosystems can now mimic the lighting schedule of an arbitrary target location: 
  sun times are cached per place, allowing non-fixed lighting methods to follow any
  configured coordinates rather than just the home location

---

## 0.6.3 — 2024-01-23

- `ActuatorHub` introduced: actuator management extracted from a plain dict into a
  dedicated class
- PIDs moved from subroutines into `ActuatorHandler`, co-located with the actuators they
  control
- `VirtualEcosystem` moved inside `Ecosystem`; `VirtualWorld` moved inside `Engine`;
  all sensors and actuators now supported in virtualization
- `VirtualEcosystem` configurable from `GaiaConfig`
- Background loop for `Climate` routine
- Click-based CLI introduced (`gaia` command)
- `Engine` pause/resume implemented: `stop()`, `resume()`, and `shutdown()` now 
  distinct, with `_running_event` / `_cleaning_up_event` to track state separately
- Fix: database is now loaded before the message broker to prevent race conditions 
  where a registration acknowledgment could arrive before the database was ready
- `SingletonMeta.detach_instance()` added
- Chaos configuration moved into `EngineConfig` and `EcosystemConfig`

---

## 0.6.2 — 2023-07-23

- Optional dependencies introduced: `camera`, `database`, `dispatcher` — Gaia
  degrades gracefully when they are absent
- Pydantic upgraded to v2
- Python 3.7 and 3.8 support dropped
- ENS160 sensor added (CO₂ / TVOC)
- Buffered sensor data sent to Ouranos after reconnection

---

## 0.6.1 — 2023-07-12

- CRUD events extended with per-section config methods (introduced in 0.6.0); only 
  the modified config section is now sent after each CRUD operation
- Config file access and modification secured
- Fix: `ActuatorHandler` status setter was not updating the internal `_actuators_state`
  dict, causing the in-memory state to diverge from the actual actuator state

---

## 0.6.0 — 2023-06-30

First release tracked in git (earlier versions were managed by hand).

The centrepiece of this release is a **complete overhaul** of the codebase: `gaiaEngine`
renamed to `Gaia`, `enginesManager` to `Engine`, the old per-ecosystem `Engine` to
`Ecosystem`, all `gaia[Subroutine]` to `[Subroutine]`;
`SubroutineTemplate` made an abstract base class; Socket.IO namespace extracted 
into an `Events` class usable with both Socket.IO and the `event-dispatcher` broker.
Most importantly, the `Climate` subroutine becomes **fully functional** for the 
first time — it was only a stub in 0.5.x — with PID-based regulation of heaters, 
coolers, humidifiers, dehumidifiers and fans. `Sensors` now returns **average values** 
across all sensors of the same type rather than raw per-sensor readings.

- `ActuatorHandler` introduced: centralises ecosystem actuator state with automatic
  mode and timer management; actuator status changes now emitted to Ouranos from
  `ActuatorHandler` rather than from individual subroutines
- `actuator_data` event added to send actuator state to Ouranos
- First version of CRUD events for remote config management
- Config changes in `ecosystems.cfg` / `private.cfg` are now sent to Ouranos
  automatically
- Pydantic used for ecosystem and private config validation
- `gaia-validators` package introduced for data types shared between Gaia and Ouranos
- SQLAlchemy-based database logging: sensor data saved to a `sensors_history` table
- Hardware objects are now singletons keyed by uid, allowing multiple subroutines to
  share the same hardware instance
- TCA9548A I2C multiplexer supported
- AHT20 and VCNL4040 sensors added
- Lighting info (`light_info`, `light_method`) moved from `Light` subroutine to
  `Ecosystem`
- Gaia can now be run as a system service

---

## 0.5.1

- Config file changes detected by the watchdog now automatically sync light 
  schedules across all ecosystems
- Socket.IO events refactored to go through namespace handlers rather than raw
  `emit()` calls
- Config data always read fresh from the singleton rather than stored as instance
  references, avoiding stale values after a config reload

---

## 0.5.0

First standalone Gaia release. Gaia and Ouranos were previously a single combined 
project (versions 0.0.1–0.4.0); 0.5.0 is the split, taking the `gaiaEngine` part 
as its own project.

- `enginesManager` / `Engine` architecture: the manager is a singleton that
  orchestrates one `Engine` per configured ecosystem, enforcing a
  single-engine-per-ecosystem constraint and hosting the scheduler, sun time cache,
  and config watchdog
- Socket.IO `retryClient` with exponential backoff reconnection for communication
  with Ouranos
- Physics-based simulation: `VirtualWorld` models daily and seasonal cycles using 
  sin/cos temperature curves; `VirtualEcosystem` simulates a contained environment 
  with heat capacity, insulation U-values and air/water ratios — actuators drive 
  the internal state directly
- Hardware compatibility layer for running on non-Raspberry Pi platforms
- All subroutines present (Sensors, Light, Climate, Health) — Climate is not yet
  functional (stubs only)
- No database logging yet

---

*Versions 0.1.0–0.4.0 are from the combined Gaia + Ouranos era. Only the gaiaEngine 
parts are described below.*

---

## 0.4.0 — March 2020

- Database abstraction layer introduced (`gaiaDatabase`): centralised MySQL operations
  with automatic table creation, connection pooling, and a consistent schema
- Hardware abstraction added (`gaiaTools`): GPIO pin translation utilities (BCM ↔ BOARD)
- System resource monitoring added (CPU / RAM / disk via `psutil`)
- Web layer (`gaiaWeb` / Flask) removed from the core engine
- Per-module logger instances replace the global logging config
- Light control gains twilight window calculations (`twilight_begin` / `twilight_end`)

---

## 0.3.0 — February 2020

- **Major refactor to a class-based, modular architecture**: `gaiaEngine` becomes the
  central orchestrator; subroutines split into independent classes (`gaiaLight`,
  `gaiaSensors`, `gaiaWeather`)
- APScheduler introduced for background task scheduling (replaces manual threading)
- Multi-ecosystem support: config system refactored to manage multiple ecosystem
  instances
- Stub subsystems added for future expansion: `gaiaHealth`, `gaiaNotification`, `gaiaORM`
- Flask-based web layer (`gaiaWeb`) introduced as a separate module

---

## 0.2.0 — December 2019

- YAML-based configuration (`ruamel.yaml`) replaces INI files; `gaiaConfig` class
  introduced
- Database operations extracted to a dedicated `sensors_to_db` module
- Weather/sunrise data fetched from an external API and cached (`sunrise_to_cache`)

---

## 0.1.0 — September 2019

Initial version, a single combined Gaia + Ouranos project:

- Single-file monolithic prototype (`Gaia.py`) with INI config and direct RPi.GPIO calls
- DHT22 sensor support with inline database scripts
- `Chaos` randomisation system for simulating natural environment variation
