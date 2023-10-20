from typing import Type

from gaia.hardware.abc import Hardware
from gaia.hardware.actuators.GPIO import gpio_actuator_models
from gaia.hardware.actuators.virtual import virtual_actuator_models


actuator_models: dict[str, Type[Hardware]] = {
    **gpio_actuator_models,
    **virtual_actuator_models,
}