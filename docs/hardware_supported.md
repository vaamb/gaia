# Hardware supported

A few sensors and actuators models are currently supported by Gaia. Most require the 
installation of additional packages to properly function.

## Sensors
- DHT11 and DHT22: require 'adafruit-circuitpython-dht' python module and 
  'libgpiod2' linux interface.
- VEML7700: requires 'adafruit-circuitpython-veml7700' python module.
- STEMMA Soil Sensor: requires 'adafruit-circuitpython-seesaw' python module.

## Actuators
- GPIO driven switches: model name used: "gpioSwitch".
- GPIO driven switches with GPIO PWM: model name used: "gpioDimmable".
