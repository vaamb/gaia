from typing import Type, TypedDict

from gaia.hardware.abc import Hardware
from gaia.hardware.actuators import actuator_models
from gaia.hardware.actuators.GPIO import (
    gpioDimmable, gpioSwitch, gpio_actuator_models)
from gaia.hardware.actuators.virtual import (
    virtualgpioDimmable, virtualgpioSwitch, virtual_actuator_models)
from gaia.hardware.camera import camera_models
from gaia.hardware.multiplexers import multiplexer_models, TCA9548A
from gaia.hardware.sensors import sensor_models
from gaia.hardware.sensors.GPIO import DHT11, DHT22, gpio_sensor_models
from gaia.hardware.sensors.I2C import (
    CapacitiveMoisture, i2c_sensor_models, VEML7700)
from gaia.hardware.sensors.virtual import (
    virtualCapacitiveMoisture, virtualDHT11, virtualDHT22, virtual_sensor_models,
    virtualVEML7700)


hardware_models: dict[str, Type[Hardware]] = {
    **actuator_models,
    **camera_models,
    **sensor_models,
}


class HardwareSpec(TypedDict):
    name: str
    interface: str
    capabilities: list[str]


def get_hardware_specs() -> list[HardwareSpec]:
    def get_interface(hardware_cls: type[Hardware]) -> str:
        interface = [
            base.__dict__["interface"]
            for base in hardware_cls.__mro__
            if "interface" in base.__dict__
        ]
        assert len(interface) == 1, hardware_cls.__name__
        return interface[0]

    def get_capabilities(hardware_cls: type[Hardware]) -> list[str]:
        capabilities = [
            base.__dict__["capability"]
            for base in hardware_cls.__mro__
            if "capability" in base.__dict__
        ]
        assert len(capabilities) >= 1, hardware_cls.__name__
        return sorted(capabilities)

    return [
        {
            "name": name,
            "interface": get_interface(hardware),
            "capabilities": get_capabilities(hardware),
        }
        for name, hardware in hardware_models.items()
        if not name.startswith("virtual")
    ]
