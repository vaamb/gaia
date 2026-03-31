Gaia
====

Gaia is an automation client for controlling and monitoring enclosed plant growth
environments — greenhouses, terrariums, aquariums, or any chamber where temperature,
humidity, light, and CO₂ matter. It runs on a Raspberry Pi, reads sensors, drives
actuators, manages lighting schedules, and runs a PID-based climate controller.

Gaia is a standalone app. It can also connect to
[Ouranos](https://github.com/vaamb/ouranos-core), a companion server that provides
a web UI, long-term data archiving, weather integration, and multi-instance
management. When disconnected, Gaia logs data locally and syncs with Ouranos when
the connection is restored.

Part of the [gaia-ouranos](https://github.com/vaamb/gaia-ouranos) ecosystem.

---

Features
--------

- **Sensors** — reads temperature, humidity, light intensity, and soil moisture
- **Actuators** — controls GPIO switches and PWM dimmers (lights, heaters, fans ...)
- **Light scheduling** — automatic sunrise/sunset-based or fixed day/night cycles
- **Climate control** — hysteresis PID controller for temperature and humidity
- **Plant health** — periodic photos and image-based health metrics via PiCamera
- **WebSocket hardware** — supports remote actuators and sensors over WebSocket
  (e.g. ESP32 devices)
- **Local persistence** — sensor data stored in a local SQLite database; synced to
  Ouranos when available
- **Virtual ecosystem** — software simulation for testing without physical hardware

---

Hardware supported
------------------

See [docs/hardware_supported.md](docs/hardware_supported.md) for the full list of
supported sensors, actuators, and cameras.

Architecture
------------

See [docs/structure.md](docs/structure.md) for an overview of Gaia's internal
structure.

---

Requirements
------------

- Python 3.11+
- A Raspberry Pi (tested on Zero, 3B+) or any Linux machine for development
- `uv` — used for dependency management and running the app

On a Raspberry Pi, install system dependencies first:

```bash
sudo apt update && sudo apt install -y libffi-dev libssl-dev
```

Then install `uv` if not already present:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

Installation
------------

Copy the install script from the `scripts/` directory and run it in the directory
where you want to install Gaia:

```bash
bash install.sh
```

The script will clone the repository, set up a `uv`-managed virtual environment,
install dependencies, and optionally configure a systemd service for running Gaia
on boot.

To install with all optional extras (camera, database, dispatcher):

```bash
bash install.sh
cd gaia
uv sync --all-packages --all-extras --no-extra test
```

---

Running
-------

If installed as a systemd service:

```bash
sudo systemctl start gaia.service
sudo systemctl enable gaia.service   # start on boot
```

Using the CLI directly:

```bash
gaia start
gaia stop
gaia restart
gaia status
```

---

Development
-----------

Clone the repository and install dependencies with test extras:

```bash
git clone https://github.com/vaamb/gaia.git
cd gaia
uv sync --all-extras
```

Run the test suite:

```bash
uv run pytest tests/ -v
```

Lint and type-check:

```bash
uvx ruff check .
uvx ty check src/
```

---

Status
------

Active. Running in production on a Raspberry Pi at home since 2020. The core data
flow is stable; APIs may still change. Requires Ouranos for the full feature set
but works as a standalone logger and controller.
