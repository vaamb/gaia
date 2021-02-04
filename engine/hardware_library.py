import logging
import random
import time

# for testing on computer purpose
try:
    from adafruit_blinka.microcontroller.bcm283x.pin import Pin
except ImportError:
    class Pin:
        def __init__(self, bcm_nbr):
            self._id = bcm_nbr
            self._mode = 0
            self._value = 0

        def init(self, mode):
            self._mode = mode

        def value(self, val):
            if val:
                self._value = val
            else:
                return self._value

import adafruit_veml7700  # adafruit-circuitpython-veml7700
import adafruit_dht  # adafruit-circuitpython-dht + sudo apt-get install libgpiod2
import board
import busio

from .utils import get_dew_point, get_absolute_humidity, \
    temperature_converter, pin_translation


sensorLogger = logging.getLogger("eng.hardware_lib")


i2c = busio.I2C(board.SCL, board.SDA)


def address_to_hex(address: str) -> int:
    if address in ["def", "default", "DEF", "DEFAULT"]:
        return 0
    return int(address, base=16)


class hardware:
    def __init__(self, **kwargs) -> None:
        self._uid = kwargs.pop("hardware_uid")
        self._address = kwargs.pop("address", "").split("_")
        self._model = kwargs.pop("model", None)
        self._name = kwargs.pop("name", self._uid)
        self._level = kwargs.pop("level", "environment")

    def __repr__(self):
        return f"<{self._uid} | {self._name} | {self._model}>"

    @property
    def uid(self) -> str:
        return self._uid

    @property
    def address(self) -> list:
        return self._address

    @property
    def model(self) -> str:
        return self._model

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, new_name: str) -> None:
        self._name = new_name

    @property
    def level(self) -> str:
        return self._level


class gpioHardware(hardware):
    IN = 0
    OUT = 1

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        assert self._address[0].lower() in ("gpio", "bcm")
        assert len(self._address) > 1
        self._pin = None
        self.set_pin()

    def set_pin(self):
        pin_bcm = pin_translation(int(self._address[1]), "to_BCM") \
            if self._address[0].lower() == "gpio" \
            else self._address[1]
        self._pin = Pin(pin_bcm)


# TODO: handle multiplex
class i2cHardware(hardware):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        assert self._address[0].lower() == "i2c"
        self._multiplexed = True if len(self._address) > 2 else False

        self._hex_address = address_to_hex(self._address[1])
        if self._multiplexed:
            self._hex_address2 = address_to_hex(self._address[2])


class gpioSwitch(gpioHardware):
    MODEL = "gpioSwitch"

    def __init__(self, **kwargs) -> None:
        # uncomment if you want to overwrite the name of model
        #kwargs["model"] = self.MODEL
        super().__init__(**kwargs)
        self._pin.init(mode=self.OUT)

    def turn_on(self) -> None:
        self._pin.value(val=1)

    def turn_off(self) -> None:
        self._pin.value(val=0)


class baseSensor(hardware):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._measure = kwargs.pop("measure", [])

    def get_data(self) -> dict:
        return {}

    @property
    def measure(self) -> list:
        return self._measure

    @measure.setter
    def measure(self, new_measure: list) -> None:
        self._measure = new_measure


# ---------------------------------------------------------------------------
#   GPIO sensors
# ---------------------------------------------------------------------------
class gpioSensor(baseSensor, gpioHardware):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class DHTSensor(gpioSensor):
    def __init__(self, **kwargs) -> None:
        if not kwargs.get("measure", []):
            kwargs["measure"] = ["temperature", "humidity"]
        super().__init__(**kwargs)

        self._unit = kwargs.pop("unit", "celsius")

        if self._model.upper() == "DHT11":
            self._device = adafruit_dht.DHT11(self._pin)
        elif self._model.upper() == "DHT12":
            self._device = adafruit_dht.DHT22(self._pin)
        else:
            raise Exception("Unknown DHT model")

    def get_data(self) -> dict:
        data = {}
        for retry in range(3):
            try:
                self._device.measure()
                humidity = round(self._device.humidity, 2)
                temperature = round(self._device.temperature, 2)

                if "humidity" in self._measure:
                    data["humidity"] = humidity

                if "temperature" in self._measure:
                    data["temperature"] = \
                        temperature_converter(temperature, "celsius",
                                              self._unit)

                if "get_dew_point" in self._measure:
                    dew_point = get_dew_point(temperature, humidity)
                    data["dew_point"] = \
                        temperature_converter(dew_point, "celsius", self._unit)

                if "absolute_humidity" in self._measure:
                    absolute_humidity = get_absolute_humidity(temperature,
                                                              humidity)
                    data["absolute_humidity"] = \
                        temperature_converter(absolute_humidity, "celsius",
                                              self._unit)
                break

            except RuntimeError as e:
                print(e)
                time.sleep(2)
                continue

            except Exception as e:
                sensorLogger.error(
                    f"Sensor {self._name} encountered an error. "
                    f"Error message: {e}")
                data = {}
                break

        return data


class DHT11(DHTSensor):
    MODEL = "DHT11"

    def __init__(self, **kwargs) -> None:
        kwargs["model"] = self.MODEL
        super().__init__(**kwargs)


class DHT22(DHTSensor):
    MODEL = "DHT22"

    def __init__(self, **kwargs) -> None:
        kwargs["model"] = self.MODEL
        super().__init__(**kwargs)


# ---------------------------------------------------------------------------
#   I2C sensors
# ---------------------------------------------------------------------------
class i2cSensor(baseSensor, i2cHardware):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class VEML7700(i2cSensor):
    MODEL = "VEML7700"

    def __init__(self, **kwargs) -> None:
        kwargs["model"] = self.MODEL
        super().__init__(**kwargs)

        if not self._hex_address:
            self._hex_address = 0x10
        self._device = adafruit_veml7700.VEML7700(i2c, self._hex_address)

    def get_data(self) -> dict:
        data = {}
        try:
            data["light"] = self._device.lux
        except Exception as e:
            sensorLogger.error(
                f"Sensor {self._name} encountered an error. "
                f"Error message: {e}")
        return data


# ---------------------------------------------------------------------------
#   Virtual sensors
# ---------------------------------------------------------------------------
class virtualGPIO(gpioSensor):
    pass


class virtualDHT(virtualGPIO):
    def get_data(self) -> dict:
        time.sleep(2)
        return {
            "temperature": round(random.uniform(17, 30), 1),
            "humidity": round(random.uniform(20, 55), 1),
        }


class virtualDHT11(virtualDHT):
    MODEL = "virtualDHT11"


class virtualDHT22(virtualDHT):
    MODEL = "virtualDHT22"


class virtualI2C(i2cSensor):
    pass


class virtualVEML7700(virtualI2C):
    MODEL = "virtualVEML7700"

    def get_data(self) -> dict:
        time.sleep(0.1)
        return {
            "light": random.randrange(1000, 100000, 10),
        }


class virtualMega(baseSensor):
    MODEL = "virtualMega"

    def get_data(self) -> dict:
        return {
            "temperature": round(random.uniform(17, 30), 1),
            "humidity": round(random.uniform(20, 55), 1),
            "light": random.randrange(1000, 100000, 10),
        }


class virtualMoisture(baseSensor):
    MODEL = "virtualMoisture"

    def get_data(self) -> dict:
        return {
            "moisture": round(random.uniform(10, 55), 1),
        }


GPIO_SENSORS = {hardware.MODEL: hardware for hardware in
                [DHT11,
                 DHT22]}

I2C_SENSORS = {hardware.MODEL: hardware for hardware in
               [VEML7700]}

VIRTUAL_SENSORS = {hardware.MODEL: hardware for hardware in
                   [virtualDHT11,
                    virtualDHT22,
                    virtualVEML7700,
                    virtualMega,
                    virtualMoisture]}

SENSORS_AVAILABLE = {**VIRTUAL_SENSORS,
                     **GPIO_SENSORS,
                     **I2C_SENSORS}

GPIO_ACTUATOR = {hardware.MODEL: hardware for hardware in
                 [gpioSwitch]}

ACTUATOR_AVAILABLE = {**GPIO_ACTUATOR}

HARDWARE_AVAILABLE = {**ACTUATOR_AVAILABLE,
                      **SENSORS_AVAILABLE}
