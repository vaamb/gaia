import math
from typing import Type

import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.hardware import hardware_models
from gaia.hardware.abc import (
    BaseSensor, Camera, Dimmer, gpioHardware, Hardware, i2cHardware, Measure,
    OneWireHardware, PlantLevelHardware, Switch, Unit)
from gaia.hardware.camera import PiCamera
from gaia.hardware.sensors.virtual import virtualDHT22
from gaia.utils import create_uid

from .data import i2c_sensor_ens160_uid, i2c_sensor_veml7700_uid, sensor_uid


@pytest.mark.asyncio
async def test_hardware_models(ecosystem: Ecosystem):
    for hardware_cls in hardware_models.values():
        # Create required config
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
        elif issubclass(hardware_cls, OneWireHardware):
            hardware_cfg["address"] = "onewire_default"
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
                measure.name
                for measure in hardware_cls.measures_available.keys()
            ]
        # Setup plants
        if issubclass(hardware_cls, PlantLevelHardware):
            hardware_cfg["level"] = gv.HardwareLevel.plants
            hardware_cfg["plants"] = ["VirtualTestPlant"]
        else:
            hardware_cfg["level"] = gv.HardwareLevel.environment

        # Test hardware
        hardware = hardware_cls.from_hardware_config(
            gv.HardwareConfig(**hardware_cfg), ecosystem=ecosystem)
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


@pytest.mark.asyncio
async def test_virtual_sensor(ecosystem: Ecosystem):
    sensor: virtualDHT22 = ecosystem.hardware[sensor_uid]
    measures, sensor._measures = sensor.measures, {Measure.temperature: Unit.celsius_degree}

    # Virtual ecosystem measure is cached for 5 seconds and virtualized sensors
    # will use this value. However, non-virtualized sensors will output random
    # values that will eventually be out of range of the virtual ecosystem.
    for _ in range(5):
        record = await sensor.get_data()
        temperature_sensor = record[0].value
        ecosystem.virtual_self.measure()
        temperature_virtual = ecosystem.virtual_self.temperature
        assert math.isclose(temperature_sensor, temperature_virtual, rel_tol=0.05)

    sensor._measures = measures


def test_i2c_address_injection(ecosystem: Ecosystem):
    for hardware_uid in (i2c_sensor_ens160_uid, i2c_sensor_veml7700_uid):
        hardware = ecosystem.hardware[hardware_uid]
        assert hardware.address_book.primary.main not in ("default", "def", 0x0)
        assert hardware.address_book.primary.multiplexer_address != 0x0

        if hardware.address_book.secondary is not None:
            assert hardware.address_book.secondary.main not in ("default", "def", 0x0)
            assert hardware.address_book.secondary.multiplexer_address != 0x0
