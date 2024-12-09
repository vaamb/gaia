from asyncio import sleep
from datetime import datetime, timezone
from math import isclose
from time import monotonic
import uuid

import pytest
from sqlalchemy import delete

import gaia_validators as gv

from gaia import Ecosystem, EngineConfig
from gaia.config.from_files import PrivateConfigValidator
from gaia.database.models import ActuatorBuffer, SensorBuffer
from gaia.events import Events as Events_

from ..data import (
    ecosystem_name,
    ecosystem_uid,
    engine_uid,
    IO_dict,
    lighting_start,
    lighting_stop,
    place_latitude,
    place_longitude,
    place_name,
    sensor_uid,
)
from ..utils import get_logs_content, MockDispatcher


class Events(Events_):
    _dispatcher: MockDispatcher


@pytest.mark.asyncio
async def test_on_pong(events_handler: Events):
    await events_handler.on_pong()

    assert isclose(events_handler._last_heartbeat, monotonic(), abs_tol=0.01)


@pytest.mark.asyncio
async def test_on_connect(events_handler: Events):
    await events_handler.on_connect(None)

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "Connection to the message broker successful" in logs

    response = events_handler._dispatcher.emit_store[0]

    assert response["event"] == "register_engine"
    assert response["data"]["engine_uid"] == engine_uid

    if events_handler._ping_task is not None:
        events_handler._ping_task.cancel()


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
async def test_on_registration_ack(
        engine_config: EngineConfig,
        events_handler: Events
):
    engine_config._private_config = PrivateConfigValidator(**{
        "places": {
            place_name: gv.Coordinates(
                latitude=place_latitude,
                longitude=place_longitude,
            ),
        },
    }).model_dump()

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

    hardware = responses[4]
    assert hardware["event"] == "hardware"
    assert hardware["data"][0]["uid"] == ecosystem_uid
    for h in hardware["data"][0]["data"]:
        h: gv.HardwareConfig
        hardware_uid = h["uid"]
        assert h["uid"] in IO_dict.keys()
        assert h["name"] == IO_dict[hardware_uid]["name"]
        assert h["address"] == IO_dict[hardware_uid]["address"]
        assert h["model"] == IO_dict[hardware_uid]["model"]
        assert h["type"] == IO_dict[hardware_uid]["type"]
        assert h["level"] == IO_dict[hardware_uid]["level"]

    actuators_data = responses[5]
    assert actuators_data["event"] == "actuators_data"
    assert actuators_data["data"][0]["uid"] == ecosystem_uid
    for actuator_record in actuators_data["data"][0]["data"]:
        actuator_record: gv.ActuatorStateRecord
        actuator_type = gv.HardwareType(actuator_record[0])
        assert actuator_type & gv.HardwareType.actuator
        assert actuator_record[1] is False
        assert actuator_record[2] == gv.ActuatorMode.automatic
        assert actuator_record[3] is False

    light_data = responses[6]
    assert light_data["event"] == "light_data"
    assert light_data["data"][0]["uid"] == ecosystem_uid
    assert light_data["data"][0]["data"]["morning_start"] == lighting_start
    assert light_data["data"][0]["data"]["evening_end"] == lighting_stop
    #assert light_data["data"][0]["data"]["method"] == lighting_method

    initialized_event = responses[7]
    assert initialized_event["event"] == "initialization_data_sent"


@pytest.mark.asyncio
async def test_on_initialized_ack(events_handler: Events):
    await events_handler.on_initialization_ack(None)

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "Ouranos successfully received ecosystems info" in logs

    await events_handler.on_initialization_ack(["base_info"])

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "Non-received info: base_info" in logs


@pytest.mark.asyncio
async def test_on_turn_actuator(events_handler: Events, ecosystem: Ecosystem):
    await ecosystem.enable_subroutine("light")
    await ecosystem.start_subroutine("light")

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        pass  # To clean up logs

    actuator = gv.HardwareType.light
    handler = ecosystem.actuator_hub.get_handler(actuator)
    mode = handler.mode
    current_state = handler.status
    countdown = 0.5

    payload_mode = gv.ActuatorModePayload.on
    turn_actuator_payload = gv.TurnActuatorPayloadDict(**{
        "ecosystem_uid": ecosystem_uid,
        "actuator": actuator,
        "mode": payload_mode,
        "countdown": countdown,
    })
    await events_handler.on_turn_actuator(turn_actuator_payload)

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "Received 'turn_actuator' event" in logs
        assert actuator.name in logs
        assert payload_mode.name in logs

    handler = ecosystem.actuator_hub.get_handler(actuator)
    assert handler.status is current_state
    assert handler.mode is mode
    assert isclose(handler.countdown, countdown, abs_tol=0.01)

    await sleep(countdown + 0.01)

    assert handler.status is True
    assert handler.mode is gv.ActuatorMode.manual
    assert handler.countdown is None

    await ecosystem.disable_subroutine("light")
    await ecosystem.stop_subroutine("light")


@pytest.mark.asyncio
async def test_on_change_management(events_handler: Events, ecosystem: Ecosystem):
    assert not ecosystem.config.get_management("camera")

    await events_handler.on_change_management({
        "uid": ecosystem_uid,
        "data": {"camera": True},
    })

    assert ecosystem.config.get_management("camera")


@pytest.mark.asyncio
async def test_send_buffered_data_and_ack(events_handler: Events, ecosystem: Ecosystem):
    ecosystem.config.set_management("database", True)
    ecosystem.engine.config.app_config.USE_DATABASE = True
    await ecosystem.engine.init_database()

    # Log some buffered data
    async with ecosystem.engine.db.scoped_session() as session:
        await session.execute(delete(SensorBuffer))
        await session.execute(delete(ActuatorBuffer))

        now = datetime.now(timezone.utc)
        session.add(
            SensorBuffer(
                ecosystem_uid=ecosystem_uid,
                sensor_uid=sensor_uid,
                measure="temperature",
                timestamp=now,
                value="21.0",
            )
        )
        session.add(
            ActuatorBuffer(
                ecosystem_uid=ecosystem_uid,
                type=gv.HardwareType.light,
                timestamp=now,
                active=True,
                mode=gv.ActuatorMode.automatic,
                status=True,
                level=None,
            )
        )

        await session.commit()

    # Test the sending event
    await events_handler.send_buffered_data()

    assert len(events_handler.dispatcher.emit_store) == 2
    sensors_data = events_handler.dispatcher.emit_store[0]
    assert sensors_data["event"] == "buffered_sensors_data"
    assert sensors_data["data"]["data"][0][0] == ecosystem_uid
    assert sensors_data["data"]["data"][0][1] == sensor_uid
    sensors_data_uuid = sensors_data["data"]["uuid"]

    actuators_data = events_handler.dispatcher.emit_store[1]
    assert actuators_data["event"] == "buffered_actuators_data"
    assert actuators_data["data"]["data"][0][0] == ecosystem_uid
    assert actuators_data["data"]["data"][0][1] == gv.HardwareType.light
    actuators_data_uuid = actuators_data["data"]["uuid"]

    # Test the acknowledgment event
    await events_handler.on_buffered_data_ack({
        "uuid": sensors_data_uuid,
        "status": gv.Result.success,
        "message": None,
    })

    async with ecosystem.engine.db.scoped_session() as session:
        remaining_sensors_data = await SensorBuffer.get_buffered_data(session)
        async for _ in remaining_sensors_data:
            assert False

    await events_handler.on_buffered_data_ack({
        "uuid": actuators_data_uuid,
        "status": gv.Result.success,
        "message": None,
    })

    async with ecosystem.engine.db.scoped_session() as session:
        remaining_actuators_data = await ActuatorBuffer.get_buffered_data(session)
        async for _ in remaining_actuators_data:
            assert False


@pytest.mark.asyncio
async def test_send_picture_arrays(events_handler: Events, ecosystem: Ecosystem):
    pictures_subroutine = ecosystem.subroutines["pictures"]
    pictures_subroutine.config.set_management("camera", True)
    pictures_subroutine.enable()
    await pictures_subroutine.start()
    await pictures_subroutine.routine()

    assert not isinstance(pictures_subroutine.picture_arrays, gv.Empty)

    await events_handler.send_picture_arrays()

    response = events_handler._dispatcher.emit_store[0]

    assert response["namespace"] == "aggregator-stream"
    assert response["event"] == "picture_arrays"
    assert isinstance(response["data"], (bytes, bytearray))
