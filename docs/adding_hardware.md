# Adding new hardware

Adding a new sensor or actuator to Gaia involves three steps:

1. Write the class
2. Register it in the models dict
3. Add a compatibility stub for non-Raspberry Pi environments (tests, dev machines)

The existing implementations in `src/gaia/hardware/sensors/` and
`src/gaia/hardware/actuators/` are the best reference.

---

## Concepts

### Address mixins

Every hardware class must inherit exactly one address mixin, which determines
how Gaia connects to the device:

| Mixin | Address format | Use for |
|---|---|---|
| `gpioAddressMixin` | `GPIO_<pin>` | GPIO-connected devices |
| `i2cAddressMixin` | `I2C_default` or `I2C_<bus>` | I2C devices |
| `OneWireAddressMixin` | `ONEWIRE_<hex>` | 1-Wire devices |
| `WebSocketAddressMixin` | `WS_<host>:<port>` | Remote WebSocket devices |

### Type mixins

Every hardware class must also inherit at least one type mixin:

| Mixin | Use for |
|---|---|
| `SensorMixin` | Sensors (reads data) |
| `ActuatorMixin` | Actuators (receives commands) |
| `SwitchMixin` | On/off actuators |
| `DimmerMixin` | PWM/level actuators |
| `DimmableSwitchMixin` | Combined switch + dimmer |
| `CameraMixin` | Camera devices |

`Hardware.__init_subclass__` enforces these rules at class-definition time and
will raise a `TypeError` if a concrete class is missing either mixin.

### Sensor base classes

Rather than inheriting `SensorMixin` directly, prefer the typed base classes in
`src/gaia/hardware/sensors/abc.py`:

| Base class | Measures | `_get_raw_data()` return type |
|---|---|---|
| `TemperatureSensor` | temperature | `float \| None` |
| `TempHumSensor` | temperature, humidity | `tuple[float \| None, float \| None]` |
| `LightSensorBase` | light | implement `_get_lux() -> float` |

For sensors that don't fit any of these, inherit `SensorMixin` directly and
define `measures_available` and `get_data()` yourself (see `ENS160` for an
example).

---

## Adding a sensor

### 1. Write the class

Create (or add to) the appropriate file under `src/gaia/hardware/sensors/`:
`GPIO.py`, `I2C.py`, or `onewire.py`.

```python
from gaia.hardware.abc import i2cAddressMixin, Sensor, Measure, Unit
from gaia.hardware.utils import is_raspi

import typing as t
if t.TYPE_CHECKING:
    from gaia.hardware.sensors._devices._compatibility import MyDeviceClass


class MySensor(i2cAddressMixin, Sensor):
    default_address = 0x53
    measures_available = {
        Measure.eco2: Unit.ppm,
        Measure.tvoc: Unit.ppm,
    }

    def _get_device(self) -> MyDeviceClass:
        if is_raspi():  # pragma: no cover
            try:
                from my_adafruit_package import MyDevice as MyDeviceClass
            except ImportError:
                raise RuntimeError(
                    "my-adafruit-package is required. Run `pip install "
                    "my-adafruit-package` in your virtual env."
                )
        else:
            from gaia.hardware.sensors._devices._compatibility import MyDeviceClass
        return MyDeviceClass(self._get_i2c(), self.address.main)

    async def get_data(self) -> list[SensorRead]:
        ...
```

### 2. Register the model

Add the class to the `*_sensor_models` dict at the bottom of its file:

```python
i2c_sensor_models: dict[str, Type[i2cSensor]] = {
    hardware.__name__: hardware
    for hardware in [
        AHT20,
        MySensor,   # <-- add here
        ...
    ]
}
```

Gaia uses the class name as the model identifier in `ecosystems.cfg`.

### 3. Add a compatibility stub

On non-Raspberry Pi machines (CI, dev), the Adafruit driver is not available.
Add a stub to `src/gaia/hardware/sensors/_devices/_compatibility.py`:

```python
class MyDeviceClass:
    def __init__(self, i2c, address): ...
    humidity: float = 50.0
    temperature: float = 22.0
```

---

## Adding an actuator

The process is the same. Use the appropriate address mixin, inherit
`SwitchMixin`, `DimmerMixin`, or `DimmableSwitchMixin`, override
`_on_initialize()` and `_on_terminate()` for device setup/teardown, and
register in the `*_actuator_models` dict in
`src/gaia/hardware/actuators/__init__.py`.

See `src/gaia/hardware/actuators/GPIO.py` for a complete example.

---

## Testing

Gaia's `VirtualEcosystem` runs the full subroutine stack without physical
hardware. Tests use virtual sensors and actuators that mirror their real
counterparts. When adding new hardware, adding a virtual variant (see
`src/gaia/hardware/sensors/virtual.py` and
`src/gaia/hardware/actuators/virtual.py`) allows it to be exercised in tests
and in simulation mode.
