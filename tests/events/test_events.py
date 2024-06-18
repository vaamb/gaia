from math import isclose
from time import monotonic
import uuid

import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.events import Events as Events_

from ..data import (
    ecosystem_name, ecosystem_uid, engine_uid, heater_info, heater_uid,
    light_info, light_uid, lighting_method, lighting_start, lighting_stop,
    place_latitude, place_longitude, place_name, sensor_info, sensor_uid)
from ..utils import get_logs_content, MockDispatcher


class Events(Events_):
    _dispatcher: MockDispatcher


@pytest.mark.asyncio
async def test_on_pong(events_handler: Events):
    await events_handler.on_pong()

    assert isclose(events_handler._last_heartbeat, monotonic())


@pytest.mark.asyncio
async def test_on_connect(events_handler: Events):
    await events_handler.on_connect(None)

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "Connection to message broker successful" in logs

    response = events_handler._dispatcher.emit_store[0]

    assert response["event"] == "register_engine"
    assert response["data"]["engine_uid"] == engine_uid


@pytest.mark.asyncio
async def test_on_register(events_handler: Events):
    await events_handler.on_register()

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "Received registration request" in logs

    response = events_handler._dispatcher.emit_store[0]

    assert response["event"] == "register_engine"
    assert response["data"]["engine_uid"] == engine_uid


@pytest.mark.asyncio
async def test_on_disconnect(events_handler: Events):
    await events_handler.on_disconnect()

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "Received a disconnection request" in logs


@pytest.mark.asyncio
async def test_on_registration_ack_wrong_uuid(events_handler: Events):
    await events_handler.on_registration_ack("wrong_uid")

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "wrongly formatted registration acknowledgment" in logs

    uuid_str = uuid.uuid4().__str__()
    await events_handler.on_registration_ack(uuid_str)

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "registration acknowledgment for another dispatcher" in logs


@pytest.mark.asyncio
async def test_on_registration_ack(events_handler: Events):
    host_uid = events_handler._dispatcher.host_uid.__str__()
    await events_handler.on_registration_ack(host_uid)

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "registration successful, sending initial ecosystems info" in logs

    responses = events_handler._dispatcher.emit_store

    places_list = responses[0]
    assert places_list["event"] == "places_list"
    assert places_list["data"]["uid"] == engine_uid
    assert places_list["data"]["data"][0]["name"] == place_name
    assert places_list["data"]["data"][0]["coordinates"] == (place_latitude, place_longitude)

    base_info = responses[1]
    assert base_info["event"] == "base_info"
    assert base_info["data"][0]["uid"] == ecosystem_uid
    assert base_info["data"][0]["data"]["engine_uid"] == engine_uid
    assert base_info["data"][0]["data"]["uid"] == ecosystem_uid
    assert base_info["data"][0]["data"]["name"] == ecosystem_name

    management = responses[2]
    assert management["event"] == "management"
    assert management["data"][0]["uid"] == ecosystem_uid
    for man, value in management["data"][0]["data"].items():
        assert gv.ManagementFlags[man]
        assert value is False

    environmental_parameters = responses[3]
    assert environmental_parameters["event"] == "environmental_parameters"
    assert environmental_parameters["data"][0]["uid"] == ecosystem_uid

    def get_h_info_list(info_name: str):
        return [
            h[info_name]
            for h in (heater_info, light_info, sensor_info)
        ]

    hardware = responses[4]
    assert hardware["event"] == "hardware"
    assert hardware["data"][0]["uid"] == ecosystem_uid
    for h in hardware["data"][0]["data"]:
        assert h["uid"] in (heater_uid, light_uid, sensor_uid)
        assert h["name"] in get_h_info_list("name")
        assert h["address"] in get_h_info_list("address")
        assert h["model"] in get_h_info_list("model")
        assert h["type"] in get_h_info_list("type")
        assert h["level"] in get_h_info_list("level")

    actuator_data = responses[5]
    assert actuator_data["event"] == "actuator_data"
    assert actuator_data["data"][0]["uid"] == ecosystem_uid
    for actuator_name, actuator in actuator_data["data"][0]["data"].items():
        actuator_type = gv.HardwareType[actuator_name]
        assert actuator_type & gv.HardwareType.actuator
        assert actuator["active"] is False
        assert actuator["status"] is False
        assert actuator["mode"] == gv.ActuatorMode.automatic

    light_data = responses[6]
    assert light_data["event"] == "light_data"
    assert light_data["data"][0]["uid"] == ecosystem_uid
    assert light_data["data"][0]["data"]["morning_start"] == lighting_start
    assert light_data["data"][0]["data"]["evening_end"] == lighting_stop
    assert light_data["data"][0]["data"]["method"] == lighting_method

    initialized_event = responses[7]
    assert initialized_event["event"] == "initialized"


@pytest.mark.asyncio
async def test_on_initialized_ack(events_handler: Events):
    await events_handler.on_initialized_ack(None)

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "Ouranos successfully received ecosystems info" in logs

    await events_handler.on_initialized_ack(["base_info"])

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "Non-received info: base_info" in logs


@pytest.mark.asyncio
async def test_on_turn_actuator(events_handler: Events, ecosystem: Ecosystem):
    await ecosystem.enable_subroutine("light")
    await ecosystem.start_subroutine("light")

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        pass  # To clean up logs

    actuator = gv.HardwareType.light
    mode = gv.ActuatorModePayload.on
    turn_actuator_payload = gv.TurnActuatorPayloadDict(**{
        "ecosystem_uid": ecosystem_uid,
        "actuator": actuator,
        "mode": mode,
        "countdown": 2.0,
    })
    await events_handler.on_turn_actuator(turn_actuator_payload)

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "Received 'turn_actuator' event" in logs
        assert actuator.name in logs
        assert mode.name in logs

    light_handler = ecosystem.actuator_hub.get_handler(actuator)
    assert light_handler.active is True
    assert light_handler.status is True
    assert light_handler.mode is gv.ActuatorMode.manual
    assert isclose(light_handler.countdown, 2.0, abs_tol=0.01)

    mode = gv.ActuatorModePayload.off
    turn_actuator_payload = gv.TurnActuatorPayloadDict(**{
        "ecosystem_uid": ecosystem_uid,
        "actuator": actuator,
        "mode": mode,
        "countdown": 5.0,
    })
    await events_handler.on_turn_actuator(turn_actuator_payload)

    assert light_handler.active is True
    assert light_handler.status is False
    assert light_handler.mode is gv.ActuatorMode.manual
    assert isclose(light_handler.countdown, 5.0, abs_tol=0.01)

    mode = gv.ActuatorModePayload.automatic
    turn_actuator_payload = gv.TurnActuatorPayloadDict(**{
        "ecosystem_uid": ecosystem_uid,
        "actuator": actuator,
        "mode": mode,
        "countdown": 0.0,
    })
    await events_handler.on_turn_actuator(turn_actuator_payload)

    assert light_handler.active is True
    assert light_handler.mode is gv.ActuatorMode.automatic

    await ecosystem.disable_subroutine("light")
    await ecosystem.stop_subroutine("light")
