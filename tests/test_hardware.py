from asyncio import create_task, sleep

import math
from typing import cast, Type

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

import gaia_validators as gv

from gaia import Engine
from gaia.hardware import hardware_models
from gaia.hardware.abc import (
    _MetaHardware, Address, CameraMixin, DimmerMixin, gpioAddressMixin, GPIOAddress,
    Hardware, I2CAddress, i2cAddressMixin, InvalidAddressError, Measure, OneWireAddress,
    OneWireAddressMixin, PiCameraAddress, PiCameraAddressMixin, PlantLevelMixin,
    SensorMixin, SensorRead, SwitchMixin, Unit, WebSocketAddress, WebSocketAddressMixin,
    WebSocketHardwareManager, WebSocketMessage)
from gaia.hardware.actuators.websocket import WebSocketDimmer, WebSocketSwitch
from gaia.hardware.sensors.websocket import WebSocketSensor
from gaia.hardware.sensors.virtual import virtualDHT22
from gaia.utils import create_uid
from gaia.virtual import VirtualEcosystem

from .data import (
    ecosystem_uid, i2c_sensor_ens160_uid, i2c_sensor_veml7700_uid, IO_dict,
    sensor_uid, ws_dimmer_uid, ws_sensor_uid, ws_switch_uid)
from .utils import yield_control


WEBSOCKET_URL: str = "ws://gaia-device:gaia@127.0.0.1:19171"


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
    if issubclass(hardware_cls, gpioAddressMixin):
        base_cfg["address"] = "GPIO_12"
    elif issubclass(hardware_cls, i2cAddressMixin):
        base_cfg["address"] = "I2C_default"
    elif issubclass(hardware_cls, OneWireAddressMixin):
        base_cfg["address"] = "onewire_default"
    elif issubclass(hardware_cls, PiCameraAddressMixin):
        base_cfg["address"] = "picamera"
    elif issubclass(hardware_cls, WebSocketAddressMixin):
        base_cfg["address"] = "websocket_127.0.0.1"
    else:
        raise ValueError("Unknown hardware address")

    # Setup model
    base_cfg["model"] = hardware_cls.__name__

    # Setup type
    if issubclass(hardware_cls, SensorMixin):
        base_cfg["type"] = gv.HardwareType.sensor
    elif issubclass(hardware_cls, CameraMixin):
        base_cfg["type"] = gv.HardwareType.camera
    elif issubclass(hardware_cls, (DimmerMixin, SwitchMixin)):
        base_cfg["type"] = gv.HardwareType.light
    else:
        raise ValueError("Unknown hardware type")

    # Setup measures
    if issubclass(hardware_cls, SensorMixin):
        if hardware_cls.measures_available is not Ellipsis:
            base_cfg["measures"] = [
                measure.name
                for measure in hardware_cls.measures_available.keys()
            ]
        else:
            base_cfg["measures"] = ["temperature|°C", "humidity|%"]
    # Setup plants
    if issubclass(hardware_cls, PlantLevelMixin):
        base_cfg["level"] = gv.HardwareLevel.plants
        base_cfg["plants"] = ["VirtualTestPlant"]
    else:
        base_cfg["level"] = gv.HardwareLevel.environment

    return base_cfg


class TestAddress:
    def test_gpio_address(self):
        addr = Address.from_str("GPIO_12")
        assert isinstance(addr, GPIOAddress)
        assert addr.main == 12
        assert repr(addr) == "GPIO_12"

    def test_i2c_address_simple(self):
        addr = Address.from_str("I2C_0x10")
        assert isinstance(addr, I2CAddress)
        assert addr.main == 0x10
        assert not addr.is_multiplexed

    def test_i2c_address_multiplexed(self):
        addr = Address.from_str("I2C_0x70#1@0x10")
        assert isinstance(addr, I2CAddress)
        assert addr.main == 0x10
        assert addr.multiplexer_address == 0x70
        assert addr.multiplexer_channel == 1
        assert addr.is_multiplexed

    def test_i2c_address_default(self):
        addr = Address.from_str("I2C_default")
        assert isinstance(addr, I2CAddress)
        assert addr.main == 0

    def test_onewire_address_default(self):
        addr = Address.from_str("ONEWIRE_default")
        assert isinstance(addr, OneWireAddress)
        assert addr.main is None

    def test_onewire_address_with_id(self):
        addr = Address.from_str("ONEWIRE_d1b4570a6461")
        assert isinstance(addr, OneWireAddress)
        assert addr.main == "d1b4570a6461"

    def test_websocket_address_no_ip(self):
        addr = Address.from_str("WEBSOCKET")
        assert isinstance(addr, WebSocketAddress)
        assert addr.main is None
        assert repr(addr) == "WEBSOCKET"

    def test_websocket_address_with_ip(self):
        addr = Address.from_str("WEBSOCKET_127.0.0.1")
        assert isinstance(addr, WebSocketAddress)
        assert addr.main == "127.0.0.1"

    def test_picamera_address(self):
        addr = Address.from_str("PICAMERA")
        assert isinstance(addr, PiCameraAddress)
        assert repr(addr) == "PICAMERA"

    def test_invalid_gpio_pin_number(self):
        with pytest.raises(InvalidAddressError):
            Address.from_str("GPIO_not_a_number")

    def test_invalid_websocket_ip(self):
        with pytest.raises(InvalidAddressError):
            Address.from_str("WEBSOCKET_not_an_ip")

    def test_unknown_address_type(self):
        with pytest.raises(InvalidAddressError):
            Address.from_str("UNKNOWN_123")

    def test_address_repr_roundtrip(self):
        valid_strings = [
            "GPIO_12",
            "I2C_0x10",
            "I2C_0x70#1@0x10",
            "WEBSOCKET",
            "WEBSOCKET_127.0.0.1",
        ]
        for s in valid_strings:
            assert repr(Address.from_str(s)) == s


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hardware_cls",
    hardware_models.values(),
    ids=hardware_models.keys(),
)
async def test_hardware_methods(hardware_cls: Type[Hardware], virtual_ecosystem: VirtualEcosystem):
    # Create required config
    hardware_cfg = _get_hardware_config(hardware_cls)

    # Make sure the hardware can be initialized
    hardware = await Hardware.initialize(gv.HardwareConfig(**hardware_cfg), ecosystem_uid)
    # Make sure the hardware has the required attributes and methods
    if isinstance(hardware, gpioAddressMixin):
        assert hardware.pin
    if isinstance(hardware, i2cAddressMixin):
        assert hardware._get_i2c() is not None
    if isinstance(hardware, WebSocketAddressMixin):
        await hardware.register()
    if isinstance(hardware, PlantLevelMixin):
        assert len(hardware.plants) > 0
    if isinstance(hardware, SensorMixin):
        assert isinstance(await hardware.get_data(), list)
    if isinstance(hardware, CameraMixin):
        assert hardware.camera_dir
        assert await hardware.get_image((42, 21))
    if isinstance(hardware, DimmerMixin):
        assert isinstance(await hardware.set_pwm_level(100), bool)
    if isinstance(hardware, SwitchMixin):
        assert isinstance(await hardware.turn_on(), bool)
        assert isinstance(await hardware.turn_off(), bool)
    if isinstance(hardware, WebSocketAddressMixin):
        await hardware.unregister()
    # Perform clean-up
    await hardware.terminate()


@pytest.mark.asyncio
async def test_virtual_sensor(virtual_ecosystem: VirtualEcosystem):
    hardware_cfg = gv.HardwareConfig(**{"uid": sensor_uid, **IO_dict[sensor_uid]})
    hardware_cfg.model = f"virtual{hardware_cfg.model}"
    sensor = cast(virtualDHT22, await Hardware.initialize(hardware_cfg, ecosystem_uid))
    measures = sensor.measures
    sensor._measures = {Measure.temperature: Unit.celsius_degree}

    # Virtual ecosystem measure is cached for 5 seconds and virtualized sensors
    # will use this value. However, non-virtualized sensors will output random
    # values that will eventually be out of range of the virtual ecosystem.
    try:
        for _ in range(5):
            record = await sensor.get_data()
            temperature_sensor = record[0].value
            virtual_ecosystem.measure()
            temperature_virtual = virtual_ecosystem.temperature
            assert math.isclose(temperature_sensor, temperature_virtual, rel_tol=0.05)
    finally:
        sensor._measures = measures


@pytest.mark.asyncio
async def test_i2c_address_injection(virtual_ecosystem: VirtualEcosystem):
    for hardware_uid in (i2c_sensor_ens160_uid, i2c_sensor_veml7700_uid):
        hardware_cfg = gv.HardwareConfig(**{"uid": hardware_uid, **IO_dict[hardware_uid]})
        hardware = await Hardware.initialize(hardware_cfg, ecosystem_uid)
        assert hardware.address.main not in ("default", "def", 0x0)
        assert hardware.address.multiplexer_address != 0x0


@pytest.mark.asyncio
class TestWebsocketHardware:
    async def _connect_device(self, hardware: WebSocketAddressMixin):
        """Connect a simulated device to the WebSocket manager."""
        websocket = await connect(WEBSOCKET_URL)
        await websocket.send(hardware.uid)
        await yield_control()  # Allow for WebSocketHardwareManager background loop to spin
        return websocket

    async def test_manager(self, logs_content):
        manager = WebSocketHardwareManager()

        # Make sure the manager start and can handle connections
        await manager.start()
        websocket = await connect(WEBSOCKET_URL)
        await websocket.send("test")
        await yield_control()  # Allow for WebSocketHardwareManager background loop to spin
        with logs_content() as logs:
            assert "Device test is trying to connect" in logs

        # Stop manager
        await manager.stop()
        # Make sure the connection is closed ...
        with pytest.raises(ConnectionClosed):
            await websocket.send("test")
        # ... and that it can't be reconnected
        await sleep(0.1)  # Allow for the connection to be closed
        with pytest.raises(ConnectionRefusedError):
            await connect(WEBSOCKET_URL)

        # Make sure the manager can handle new connections once restarted
        await manager.start()
        await connect(WEBSOCKET_URL)

        # And that it can be stopped again
        await manager.stop()

    async def test_manager_errors(self):
        manager = WebSocketHardwareManager()

        # Stop before start
        with pytest.raises(RuntimeError, match="not currently running"):
            await manager.stop()

        # Start twice
        await manager.start()
        with pytest.raises(RuntimeError, match="already running"):
            await manager.start()

        await manager.stop()

    async def test_unregistered_device_rejected(self, logs_content):
        manager = WebSocketHardwareManager()
        await manager.start()

        websocket = await connect(WEBSOCKET_URL)
        await websocket.send("unknown_uid")
        await yield_control()

        with logs_content() as logs:
            assert "is trying to connect but is not registered" in logs

        with pytest.raises(ConnectionClosed):
            await websocket.recv()

        await manager.stop()

    async def test_wrong_ip_device_rejected(self, logs_content):
        fake_uid = "fake_uid_wrong_ip"
        manager = WebSocketHardwareManager()
        await manager.register_hardware(fake_uid, "192.168.1.1")
        await manager.start()

        websocket = await connect(WEBSOCKET_URL)
        await websocket.send(fake_uid)
        await yield_control()

        with logs_content() as logs:
            assert "is trying to connect from an unexpected" in logs

        with pytest.raises(ConnectionClosed):
            await websocket.recv()

        await manager.stop()

    async def test_hardware(self, logs_content):
        hardware_cfg = gv.HardwareConfig(**{"uid": ws_switch_uid, **IO_dict[ws_switch_uid]})
        hardware = cast(WebSocketSwitch, await Hardware.initialize(hardware_cfg, ecosystem_uid))

        # Test device connection
        websocket = await self._connect_device(hardware)
        with logs_content() as logs:
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
        await yield_control()
        raw_response = await websocket.recv()
        response = WebSocketMessage.model_validate_json(raw_response)
        assert response.uuid is not None
        assert response.data == msg
        # Get response, from device to Gaia
        payload = WebSocketMessage(uuid=response.uuid, data=msg * 2).model_dump_json()
        await websocket.send(payload)
        await yield_control()
        response = await task
        assert response == msg * 2

        # Test ´_send_msg_and_wait()´ timeout
        with pytest.raises(TimeoutError):
            await hardware._send_msg_and_wait(msg, timeout=0.1)

        await hardware.terminate()

    async def test_hardware_connected_property(self):
        hardware_cfg = gv.HardwareConfig(**{"uid": ws_switch_uid, **IO_dict[ws_switch_uid]})
        hardware = cast(WebSocketSwitch, await Hardware.initialize(hardware_cfg, ecosystem_uid))

        assert not hardware.connected

        websocket = await self._connect_device(hardware)
        assert hardware.connected

        await websocket.close()
        await yield_control()
        assert not hardware.connected

        await hardware.terminate()

    async def test_switch(self):
        hardware_cfg = gv.HardwareConfig(**{"uid": ws_switch_uid, **IO_dict[ws_switch_uid]})
        hardware = cast(WebSocketSwitch, await Hardware.initialize(hardware_cfg, ecosystem_uid))

        # Connect the device
        websocket = await self._connect_device(hardware)

        # Turn on
        task = create_task(hardware.turn_on())
        await yield_control()
        raw_response = await websocket.recv()
        response = WebSocketMessage.model_validate_json(raw_response)
        assert response.data == {"action": "turn_actuator", "data": "on"}

        payload = WebSocketMessage(
            uuid=response.uuid,
            data={
                "status": gv.Result.success,
                "data": True,
            }
        ).model_dump_json()
        await websocket.send(payload)
        await yield_control()
        response = await task
        assert response

        # Turn off
        task = create_task(hardware.turn_off())
        await yield_control()
        raw_response = await websocket.recv()
        response = WebSocketMessage.model_validate_json(raw_response)
        assert response.data == {"action": "turn_actuator", "data": "off"}

        payload = WebSocketMessage(
            uuid=response.uuid,
            data={
                "status": gv.Result.success,
                "data": True
            }
        ).model_dump_json()
        await websocket.send(payload)
        await yield_control()
        response = await task
        assert response

        await hardware.terminate()

    async def test_switch_failure(self):
        hardware_cfg = gv.HardwareConfig(**{"uid": ws_switch_uid, **IO_dict[ws_switch_uid]})
        hardware = cast(WebSocketSwitch, await Hardware.initialize(hardware_cfg, ecosystem_uid))

        websocket = await self._connect_device(hardware)

        task = create_task(hardware.turn_on())
        await yield_control()
        raw_response = await websocket.recv()
        response = WebSocketMessage.model_validate_json(raw_response)

        payload = WebSocketMessage(
            uuid=response.uuid,
            data={
                "status": gv.Result.failure,
                "message": "An error occurred",
            },
        ).model_dump_json()
        await websocket.send(payload)
        await yield_control()
        result = await task
        assert result is False

        await hardware.terminate()

    async def test_dimmer(self):
        hardware_cfg = gv.HardwareConfig(**{"uid": ws_dimmer_uid, **IO_dict[ws_dimmer_uid]})
        hardware = cast(WebSocketDimmer, await Hardware.initialize(hardware_cfg, ecosystem_uid))

        # Connect the device
        websocket = await self._connect_device(hardware)

        # Set PWM level
        task = create_task(hardware.set_pwm_level(42))
        await yield_control()
        raw_response = await websocket.recv()
        response = WebSocketMessage.model_validate_json(raw_response)
        assert response.data == {"action": "set_level", "data": 42}

        payload = WebSocketMessage(
            uuid=response.uuid,
            data={
                "status": gv.Result.success,
                "data": True,
            }
        ).model_dump_json()
        await websocket.send(payload)
        await yield_control()
        response = await task
        assert response

        await hardware.terminate()

    async def test_dimmer_failure(self):
        hardware_cfg = gv.HardwareConfig(**{"uid": ws_dimmer_uid, **IO_dict[ws_dimmer_uid]})
        hardware = cast(WebSocketDimmer, await Hardware.initialize(hardware_cfg, ecosystem_uid))

        websocket = await self._connect_device(hardware)

        task = create_task(hardware.set_pwm_level(42))
        await yield_control()
        raw_response = await websocket.recv()
        response = WebSocketMessage.model_validate_json(raw_response)

        payload = WebSocketMessage(
            uuid=response.uuid,
            data={
                "status": gv.Result.failure,
                "message": "An error occurred",
            },
        ).model_dump_json()
        await websocket.send(payload)
        await yield_control()
        result = await task
        assert result is False

        await hardware.terminate()

    async def test_sensor(self):
        hardware_cfg = gv.HardwareConfig(**{"uid": ws_sensor_uid, **IO_dict[ws_sensor_uid]})
        hardware = cast(WebSocketSensor, await Hardware.initialize(hardware_cfg, ecosystem_uid))

        # Connect the device
        websocket = await self._connect_device(hardware)

        task = create_task(hardware.get_data())
        await yield_control()
        raw_response = await websocket.recv()
        response = WebSocketMessage.model_validate_json(raw_response)
        assert response.data == {"action": "send_data"}

        data = [SensorRead("not_an_uid", "def_a_measure", 42)]
        payload = WebSocketMessage(
            uuid=response.uuid,
            data={
                "status": gv.Result.success,
                "data": data,
            }
        ).model_dump_json()
        await websocket.send(payload)
        await yield_control()
        response = await task
        assert response == data

        await hardware.terminate()


@pytest.mark.asyncio
async def test_cleanup(engine: Engine):
    await engine.remove_ecosystem(ecosystem_uid)
    assert not _MetaHardware.instances
