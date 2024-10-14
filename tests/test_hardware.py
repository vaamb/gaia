from typing import Type

import pytest

import gaia_validators as gv

from gaia.hardware import hardware_models
from gaia.hardware.abc import (
    BaseSensor, Camera, Dimmer, gpioHardware, Hardware, i2cHardware,
    PlantLevelHardware, Switch)
from gaia.hardware.camera import PiCamera
from gaia.utils import create_uid


@pytest.mark.asyncio
async def test_hardware_models():
    for hardware_cls in hardware_models.values():
        # Create required config
        try:
            hardware_cls: Type[Hardware]
            hardware_cfg: gv.HardwareConfigDict = {
                "uid": create_uid(16),
                "name": "VirtualTestHardware",
                "address": None,
                "model": None,
                "type": None,
                "level": None,
                "measures": [],
                "plants": [],
                "multiplexer_model": None,
            }
            # Setup address
            if issubclass(hardware_cls, gpioHardware):
                hardware_cfg["address"] = "GPIO_19&GPIO_12"
            elif issubclass(hardware_cls, i2cHardware):
                hardware_cfg["address"] = "I2C_default"
            elif issubclass(hardware_cls, PiCamera):
                hardware_cfg["address"] = "picamera"
            else:
                raise ValueError("Unknown hardware address")
            # Setup model
            hardware_cfg["model"] = hardware_cls.__name__
            # Setup type
            if issubclass(hardware_cls, BaseSensor):
                hardware_cfg["type"] = gv.HardwareType.sensor
            elif issubclass(hardware_cls, Camera):
                hardware_cfg["type"] = gv.HardwareType.camera
            elif issubclass(hardware_cls, (Dimmer, Switch)):
                hardware_cfg["type"] = gv.HardwareType.light
            else:
                raise ValueError("Unknown hardware type")
            # Setup measures
            if issubclass(hardware_cls, BaseSensor):
                hardware_cfg["measures"] = [
                    measure.name for measure in
                    hardware_cls.measures_available.keys()
                ]
            # Setup plants
            if issubclass(hardware_cls, PlantLevelHardware):
                hardware_cfg["level"] = gv.HardwareLevel.plants
                hardware_cfg["plants"] = ["VirtualTestPlant"]
            else:
                hardware_cfg["level"] = gv.HardwareLevel.environment
        except Exception as e:
            raise Exception(
                f"Error while setting up config for {hardware_cls}."
            ) from e

        # Test hardware
        try:
            hardware = hardware_cls.from_unclean(subroutine=None, **hardware_cfg)
            if isinstance(hardware, gpioHardware):
                assert hardware.pin
            if isinstance(hardware, i2cHardware):
                hardware._get_i2c()
            if isinstance(hardware, PlantLevelHardware):
                assert hardware.plants
            if isinstance(hardware, BaseSensor):
                assert await hardware.get_data()
            if isinstance(hardware, Camera):
                assert hardware.camera_dir
                assert await hardware.get_image((42, 21))
            if isinstance(hardware, Dimmer):
                await hardware.set_pwm_level(100)
            if isinstance(hardware, Switch):
                await hardware.turn_on()
                await hardware.turn_off()
            print(f"Test succeeded for hardware '{hardware}'")
        except Exception as e:
            raise Exception(f"Error while testing {hardware_cls}.") from e
