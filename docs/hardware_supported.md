# Hardware supported

A few sensors, actuators, and camera models are currently supported by Gaia.
Most require the installation of additional packages.

## Sensors

| Model | Description | Extra dependency |
|---|---|---|
| DHT11, DHT22 | GPIO temperature and humidity sensor | `adafruit-circuitpython-dht`, `libgpiod2` |
| AHT20 | I2C temperature and humidity sensor | `adafruit-circuitpython-ahtx0` |
| ENS160 | I2C air quality sensor (AQI, eCO₂, TVOC) | `adafruit-circuitpython-ens160` |
| VEML7700 | I2C ambient light sensor | `adafruit-circuitpython-veml7700` |
| VCNL4040 | I2C ambient light sensor | `adafruit-circuitpython-vcnl4040` |
| STEMMA Soil Sensor | I2C capacitive soil moisture and temperature sensor | `adafruit-circuitpython-seesaw` |
| DS18B20 | 1-Wire temperature sensor | — |

## Actuators

| Model | Description | Extra dependency |
|---|---|---|
| `gpioSwitch` | GPIO-driven on/off switch | — |
| `gpioDimmable` | GPIO-driven switch with PWM dimming | — |

## Cameras

| Model | Description | Extra dependency |
|---|---|---|
| Raspberry Pi Camera | Camera module for periodic plant health photos | `picamera2` |

## WebSocket devices

Remote hardware communicating over WebSocket is also supported. Any device
implementing the Gaia WebSocket protocol can act as a sensor or actuator.

---

Adding new hardware is straightforward — see
[docs/adding_hardware.md](adding_hardware.md) for a step-by-step guide.
