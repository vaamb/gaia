import math
from typing import Type

import pytest

import gaia_validators as gv

from gaia import Ecosystem, Engine
from gaia.hardware import hardware_models
from gaia.hardware.abc import (
    _MetaHardware, BaseSensor, Camera, Dimmer, gpioHardware, Hardware, i2cHardware,
    Measure, OneWireHardware, PlantLevelHardware, Switch, Unit)
from gaia.hardware.camera import PiCamera
from gaia.hardware.sensors.virtual import virtualDHT22
from gaia.utils import create_uid

from .data import (
    ecosystem_uid, i2c_sensor_ens160_uid, i2c_sensor_veml7700_uid, sensor_uid)


def _get_hardware_config(hardware_cls: Type[Hardware]) -> gv.HardwareConfigDict:
    base_cfg: gv.HardwareConfigDict = {
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
        base_cfg["address"] = "GPIO_12"
    elif issubclass(hardware_cls, i2cHardware):
        base_cfg["address"] = "I2C_default"
    elif issubclass(hardware_cls, OneWireHardware):
        base_cfg["address"] = "onewire_default"
    elif issubclass(hardware_cls, PiCamera):
        base_cfg["address"] = "picamera"
    else:
        raise ValueError("Unknown hardware address")

    # Setup model
    base_cfg["model"] = hardware_cls.__name__

    # Setup type
    if issubclass(hardware_cls, BaseSensor):
        base_cfg["type"] = gv.HardwareType.sensor
    elif issubclass(hardware_cls, Camera):
        base_cfg["type"] = gv.HardwareType.camera
    elif issubclass(hardware_cls, (Dimmer, Switch)):
        base_cfg["type"] = gv.HardwareType.light
    else:
        raise ValueError("Unknown hardware type")

    # Setup measures
    if issubclass(hardware_cls, BaseSensor):
        if hardware_cls.measures_available is not Ellipsis:
            base_cfg["measures"] = [
                measure.name
                for measure in hardware_cls.measures_available.keys()
            ]
        else:
            base_cfg["measures"] = ["temperature|°C", "humidity|%"]
    # Setup plants
    if issubclass(hardware_cls, PlantLevelHardware):
        base_cfg["level"] = gv.HardwareLevel.plants
        base_cfg["plants"] = ["VirtualTestPlant"]
    else:
        base_cfg["level"] = gv.HardwareLevel.environment

    return base_cfg


@pytest.mark.asyncio
async def test_hardware_methods(ecosystem: Ecosystem):
    for hardware_cls in hardware_models.values():
        # Create required config
        hardware_cls: Type[Hardware]
        hardware_cfg = _get_hardware_config(hardware_cls)

        # Make sure the hardware can be initialized
        hardware = hardware_cls._unsafe_from_config(
            gv.HardwareConfig(**hardware_cfg), ecosystem=ecosystem)
        # Make sure the hardware has the required attributes and methods
        if isinstance(hardware, gpioHardware):
            assert hardware.pin
        if isinstance(hardware, i2cHardware):
            assert hardware._get_i2c() is not None
        if isinstance(hardware, PlantLevelHardware):
            assert len(hardware.plants) > 0
        if isinstance(hardware, BaseSensor):
            assert await hardware.get_data()
        if isinstance(hardware, Camera):
            assert hardware.camera_dir
            assert await hardware.get_image((42, 21))
        if isinstance(hardware, Dimmer):
            assert isinstance(await hardware.set_pwm_level(100), bool)
        if isinstance(hardware, Switch):
            assert isinstance(await hardware.turn_on(), bool)
            assert isinstance(await hardware.turn_off(), bool)
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
        assert hardware.address.main not in ("default", "def", 0x0)
        assert hardware.address.multiplexer_address != 0x0


@pytest.mark.asyncio
async def test_cleanup(engine: Engine):
    await engine.remove_ecosystem(ecosystem_uid)
    assert not _MetaHardware.instances
