import logging
import weakref

from adafruit_platformdetect import Board, Detector

from src import utils


_RASPI = Board(Detector()).any_raspberry_pi


if _RASPI:
    import board
    import busio
    from adafruit_blinka.microcontroller.bcm283x.pin import Pin
    from picamera import PiCamera as Camera
else:
    from .compatibility import board
    from .compatibility import busio
    from .compatibility import Pin
    from .compatibility import Camera


sensorLogger = logging.getLogger("eng.hardware_lib")


_store = {}


def get_i2c():
    try:
        return _store["I2C"]
    except KeyError:
        _store["I2C"] = busio.I2C(board.SCL, board.SDA)
        return _store["I2C"]


def address_to_hex(address: str) -> int:
    if address.lower() in ["def", "default"]:
        return 0
    return int(address, base=16)


class hardware:
    """
    Base class for all hardware config creation and when creating hardware
    object from config file.
    A minimal hardware should have an uid (cf under), a name, an address,
    a model name, a type and a level.
    When creating a new hardware, use the
    specificConfig("your_environment").create_new_hardware() method. This will
    automatically generate a unique uid, properly format info and save it in
    ecosystems.cfg
    """
    def __init__(self, subroutine, uid, address, **kwargs) -> None:
        self._subroutine = weakref.proxy(subroutine)

        self._plant = kwargs.pop("plant", "")
        level = kwargs.pop("level")
        if level.lower() in ("environment", "environments"):
            self._level = "environment"
        elif level.lower() in ("plant", "plants"):
            assert self._plant, "Plants-level hardware need to be provided a " \
                                "plant name as kwarg with the key name 'plant'"
            self._level = "plants"
        else:
            raise AttributeError("level should be 'plant' or 'environment'")
        self._uid = uid
        self._name = kwargs.pop("name", self._uid)
        self._address = address
        self._model = kwargs.pop("model")
        self._type = kwargs.pop("type")
        self._measure = kwargs.pop("measure", [])

        address = self._address.split(":")
        self._main_address = address[0].split("_")
        try:
            self._secondary_address = address[1].split("_")
        except IndexError:
            self._secondary_address = None

    def __repr__(self):
        return f"<{self._uid} | {self._name} | {self._model}>"

    @property
    def uid(self) -> str:
        return self._uid

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, new_name: str) -> None:
        self._name = new_name

    @property
    def address(self) -> list:
        return self._address

    @property
    def model(self) -> str:
        return self._model

    @property
    def measure(self) -> list:
        if self._measure or (self._type == "sensor"):
            return self._measure
        # "Fake" AttributeError in case self is not a sensor and has no measure
        raise AttributeError(f"'{type(self).__qualname__}' object has no "
                             f"attribute 'measure'")

    @measure.setter
    def measure(self, new_measure: list) -> None:
        self._measure = new_measure

    @property
    def level(self) -> str:
        return self._level

    @property
    def dict_repr(self):
        rv = {
            "uid": self._uid,
            "name": self._name,
            "address": self._address,
            "model": self._model,
            "type": self._type,
            "level": self._level,
        }
        if self._measure:
            rv["measure"] = self._measure
        if self._plant:
            rv["plant"] = self._plant
        return rv


class gpioHardware(hardware):
    IN = 0
    OUT = 1

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        if not self._main_address[0].lower() in ("gpio", "bcm"):
            raise ValueError("gpioHardware address must be of type: "
                             "'GPIO_pinnumber' or 'BCM_pinnumber'")
        assert len(self._main_address) > 1
        self._pin = None
        self.set_pin()

    def set_pin(self):
        pin_bcm = utils.pin_translation(int(self._main_address[1]), "to_BCM") \
            if self._main_address[0].lower() == "gpio" \
            else int(self._main_address[1])
        try:
            utils.pin_translation(pin_bcm, "to_board")
        except KeyError:
            raise ValueError("The pin is not a valid GPIO data pin")
        self._pin = Pin(pin_bcm)


# TODO: handle multiplex
class i2cHardware(hardware):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        if not self._main_address[0].lower() == "i2c":
            raise ValueError("gpioHardware address must be of type: "
                             "'I2C_default' or 'I2C_0' to use default sensor "
                             "I2C address, or of type 'I2C_hex_address' to "
                             "use a specific hex address")
        self._multiplexed = True if len(self._main_address) > 2 else False

        self._hex_address = address_to_hex(self._main_address[1])
        if self._multiplexed:
            self._hex_address2 = address_to_hex(self._main_address[2])


class baseSensor(hardware):
    def __init__(self, **kwargs) -> None:
        kwargs["type"] = "sensor"
        super().__init__(**kwargs)

    def get_data(self) -> dict:
        return {}


class gpioSensor(baseSensor, gpioHardware):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class i2cSensor(baseSensor, i2cHardware):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
