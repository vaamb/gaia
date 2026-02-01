import asyncio
from asyncio import create_task, sleep

import math
from typing import Type

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

import gaia_validators as gv

from gaia import Ecosystem, Engine, EngineConfig
from gaia.hardware import hardware_models
from gaia.hardware.abc import (
    _MetaHardware, BaseSensor, Camera, Dimmer, gpioHardware, Hardware, i2cHardware,
    Measure, OneWireHardware, PlantLevelHardware, Switch, Unit, WebSocketHardware,
    WebSocketHardwareManager, WebSocketMessage)
from gaia.hardware.actuators.websocket import WebSocketDimmer, WebSocketSwitch
from gaia.hardware.sensors.websocket import WebSocketSensor
from gaia.hardware.camera import PiCamera
from gaia.hardware.sensors.virtual import virtualDHT22
from gaia.utils import create_uid

from .data import (
    debug_log_file, ecosystem_uid, i2c_sensor_ens160_uid, i2c_sensor_veml7700_uid,
    sensor_uid, ws_dimmer_uid, ws_sensor_uid, ws_switch_uid)
from .utils import get_logs_content


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
    elif issubclass(hardware_cls, WebSocketHardware):
        base_cfg["address"] = "websocket_127.0.0.1"
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
        if isinstance(hardware, WebSocketHardware):
            await hardware.register()
        if isinstance(hardware, PlantLevelHardware):
            assert len(hardware.plants) > 0
        if isinstance(hardware, BaseSensor):
            assert isinstance(await hardware.get_data(), list)
        if isinstance(hardware, Camera):
            assert hardware.camera_dir
            assert await hardware.get_image((42, 21))
        if isinstance(hardware, Dimmer):
            assert isinstance(await hardware.set_pwm_level(100), bool)
        if isinstance(hardware, Switch):
            assert isinstance(await hardware.turn_on(), bool)
            assert isinstance(await hardware.turn_off(), bool)
        if isinstance(hardware, WebSocketHardware):
            await hardware.unregister()
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
class TestWebsocketHardware:
    async def test_manager(self, engine_config: EngineConfig):
        manager = WebSocketHardwareManager(engine_config)

        # Make sure the manager start and can handle connections
        await manager.start()
        await sleep(0.1)  # Allow for WebSocketHardwareManager background loop to start
        websocket = await connect("ws://gaia-device:gaia@127.0.0.1:19171")
        await websocket.send("test")
        await sleep(0.1)  # Allow for WebSocketHardwareManager background loop to spin
        with get_logs_content(engine_config.logs_dir / debug_log_file) as logs:
            assert f"Device test is trying to connect" in logs

        # Stop manager
        await manager.stop()
        # Make sure the connection is closed ...
        with pytest.raises(ConnectionClosed):
            await websocket.send("test")
        # ... and that it can't be reconnected
        await sleep(0.1)  # Allow for the connection to be closed
        with pytest.raises(ConnectionRefusedError):
            await connect("ws://gaia-device:gaia@127.0.0.1:19171")

        # Make sure the manager can handle new connections once restarted
        await manager.start()
        await sleep(0.1)
        await connect("ws://gaia-device:gaia@127.0.0.1:19171")

        # And that it can be stopped again
        await manager.stop()

    async def test_hardware(self, ecosystem: Ecosystem):
        hardware: WebSocketSwitch = ecosystem.hardware[ws_switch_uid]
        # Hardware registration is taken care of by the ecosystem setup

        # Test device connection
        websocket = await connect("ws://gaia-device:gaia@127.0.0.1:19171")
        await websocket.send(hardware.uid)
        await sleep(0.1)  # Allow for WebSocketHardwareManager background loop to spin
        with get_logs_content(ecosystem.engine.config.logs_dir / debug_log_file) as logs:
            assert f"Device {hardware.uid} connected" in logs

        # Test ´_send_msg_and_forget()´
        msg = "Test msg"
        await hardware._send_msg_and_forget(msg)
        raw_response = await websocket.recv()
        response = WebSocketMessage.model_validate_json(raw_response)
        assert response.uuid is None
        assert response.data == msg

        # Test ´_send_msg_and_wait()´
        # Send message from Gaia to device
        task = create_task(hardware._send_msg_and_wait(msg))
        await sleep(0.1)
        raw_response = await websocket.recv()
        response = WebSocketMessage.model_validate_json(raw_response)
        assert response.uuid is not None
        assert response.data == msg
        # Get response, from device to Gaia
        payload = WebSocketMessage(uuid=response.uuid, data=msg * 2).model_dump_json()
        await websocket.send(payload)
        await sleep(0.1)
        response = await task
        assert response == msg * 2

        # Test ´_send_msg_and_wait()´ timeout
        with pytest.raises(TimeoutError):
            await hardware._send_msg_and_wait(msg, timeout=0.1)

        # Stop the manager as otherwise the test can hang forever
        await hardware._websocket_manager.stop()
        # Hardware unregistration is taken care of by the ecosystem teardown

    async def test_switch(self, ecosystem: Ecosystem):
        hardware: WebSocketSwitch = ecosystem.hardware[ws_switch_uid]
        # Hardware registration is taken care of by the ecosystem setup

        # Connect the device
        websocket = await connect("ws://gaia-device:gaia@127.0.0.1:19171")
        await websocket.send(hardware.uid)
        await sleep(0.1)  # Allow for WebSocketHardwareManager background loop to spin

        # Turn on
        task = create_task(hardware.turn_on())
        await sleep(0.1)
        raw_response = await websocket.recv()
        response = WebSocketMessage.model_validate_json(raw_response)
        assert response.data == {"action": "turn_actuator", "data": "on"}

        payload = WebSocketMessage(
            uuid=response.uuid,
            data={
                "status": gv.Result.success,
            }
        ).model_dump_json()
        await websocket.send(payload)
        await sleep(0.1)
        response = await task
        assert response

        # Turn off
        task = create_task(hardware.turn_off())
        await sleep(0.1)
        raw_response = await websocket.recv()
        response = WebSocketMessage.model_validate_json(raw_response)
        assert response.data == {"action": "turn_actuator", "data": "off"}

        payload = WebSocketMessage(
            uuid=response.uuid,
            data={
                "status": gv.Result.success,
            }
        ).model_dump_json()
        await websocket.send(payload)
        await sleep(0.1)
        response = await task
        assert response

        # Stop the manager as otherwise the test can hang forever
        await hardware._websocket_manager.stop()
        # Hardware unregistration is taken care of by the ecosystem teardown

    async def test_dimmer(self, ecosystem: Ecosystem):
        hardware: WebSocketDimmer = ecosystem.hardware[ws_dimmer_uid]
        # Hardware registration is taken care of by the ecosystem setup

        # Connect the device
        websocket = await connect("ws://gaia-device:gaia@127.0.0.1:19171")
        await websocket.send(hardware.uid)
        await sleep(0.1)  # Allow for WebSocketHardwareManager background loop to spin

        # Turn on
        task = create_task(hardware.set_pwm_level(42))
        await sleep(0.1)
        raw_response = await websocket.recv()
        response = WebSocketMessage.model_validate_json(raw_response)
        assert response.data == {"action": "set_level", "data": 42}

        payload = WebSocketMessage(
            uuid=response.uuid,
            data={
                "status": gv.Result.success,
            }
        ).model_dump_json()
        await websocket.send(payload)
        await sleep(0.1)
        response = await task
        assert response

        # Stop the manager as otherwise the test can hang forever
        await hardware._websocket_manager.stop()
        # Hardware unregistration is taken care of by the ecosystem teardown

    async def test_sensor(self, ecosystem: Ecosystem):
        hardware: WebSocketSensor = ecosystem.hardware[ws_sensor_uid]
        # Hardware registration is taken care of by the ecosystem setup

        # Connect the device
        websocket = await connect("ws://gaia-device:gaia@127.0.0.1:19171")
        await websocket.send(hardware.uid)
        await sleep(0.1)  # Allow for WebSocketHardwareManager background loop to spin

        task = create_task(hardware.get_data())
        await sleep(0.1)
        raw_response = await websocket.recv()
        response = WebSocketMessage.model_validate_json(raw_response)
        assert response.data == {"action": "send_data"}

        data = [gv.SensorRecord("not_an_uid", "def_a_measure", 42, None)]
        payload = WebSocketMessage(
            uuid=response.uuid,
            data={
                "status": gv.Result.success,
                "data": data,
            }
        ).model_dump_json()
        await websocket.send(payload)
        await sleep(0.1)
        response = await task
        assert response == data

        # Stop the manager as otherwise the test can hang forever
        await hardware._websocket_manager.stop()
        # Hardware unregistration is taken care of by the ecosystem teardown


@pytest.mark.asyncio
async def test_cleanup(engine: Engine):
    await engine.remove_ecosystem(ecosystem_uid)
    assert not _MetaHardware.instances
