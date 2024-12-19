from datetime import time

import pytest

import gaia_validators as gv

from gaia.events import Events as Events_

from ..data import ecosystem_uid, engine_uid, hardware_info, hardware_uid, IO_dict
from ..utils import get_logs_content, MockDispatcher


class Events(Events_):
    _dispatcher: MockDispatcher


def assert_success(events_handler: Events, expected_events_emitted: int = 2):
    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "was successfully treated" in logs
    assert len(events_handler._dispatcher.emit_store) == expected_events_emitted
    emitted_msg: gv.RequestResultDict = events_handler._dispatcher.emit_store[0]["data"]
    assert emitted_msg["status"] == gv.Result.success


@pytest.mark.asyncio
async def test_wrong_engine_uid(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": "wrong_uid", "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.create,
        target="hardware",
        data={},
    ).model_dump()

    await events_handler.on_crud(message)

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "Received a CRUD request intended to engine" in logs


@pytest.mark.asyncio
async def test_missing_ecosystem_uid(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid},
        action=gv.CrudAction.create,
        target="hardware",
        data={},
    ).model_dump()

    await events_handler.on_crud(message)

    error_msg = "Create hardware requires the 'ecosystem_uid' field to be set."
    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert error_msg in logs
    emitted_msg: gv.RequestResultDict = events_handler._dispatcher.emit_store[0]["data"]
    assert emitted_msg["status"] == gv.Result.failure
    assert error_msg in emitted_msg["message"]


@pytest.mark.asyncio
async def test_success(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.create,
        target="ecosystem",
        data={"ecosystem_name": "TestCrud"},
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)


@pytest.mark.asyncio
async def test_create_ecosystem(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.create,
        target="ecosystem",
        data={"ecosystem_name": "TestCrud"},
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update = events_handler._dispatcher.emit_store[1]["data"]
    assert len(data_update) == 2
    assert "TestCrud" in [ecosystem["data"]["name"] for ecosystem in data_update]
    assert len(events_handler.ecosystems) == 2
    assert "TestCrud" in [
        ecosystem.name
        for ecosystem in events_handler.ecosystems.values()
    ]


@pytest.mark.asyncio
async def test_update_ecosystem_failure(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid},
        action=gv.CrudAction.update,
        target="ecosystem",
        data={"ecosystem_id": "does_not_exists", "name": "NewName"},
    ).model_dump()

    await events_handler.on_crud(message)

    result_msg = events_handler._dispatcher.emit_store[0]["data"]
    assert result_msg["status"] == gv.Result.failure
    assert "Ecosystem with id 'does_not_exists' not found" in result_msg["message"]


@pytest.mark.asyncio
async def test_update_ecosystem(events_handler: Events):
    new_name = "NewName"
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid},
        action=gv.CrudAction.update,
        target="ecosystem",
        data={"ecosystem_id": ecosystem_uid, "name": new_name},
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    ecosystem_config = \
        events_handler.engine.config.ecosystems_config_dict[ecosystem_uid]
    assert ecosystem_config["name"] == new_name


@pytest.mark.asyncio
async def test_delete_ecosystem_failure(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid},
        action=gv.CrudAction.delete,
        target="ecosystem",
        data={"ecosystem_id": "does_not_exists"},
    ).model_dump()

    await events_handler.on_crud(message)

    result_msg = events_handler._dispatcher.emit_store[0]["data"]
    assert result_msg["status"] == gv.Result.failure
    assert "Ecosystem with id 'does_not_exists' not found" in result_msg["message"]


@pytest.mark.asyncio
async def test_delete_ecosystem(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.delete,
        target="ecosystem",
        data={"ecosystem_id": ecosystem_uid},
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler, 1)

    assert len(events_handler.ecosystems) == 0
    assert len(events_handler.engine.ecosystems_started) == 0
    assert len(events_handler.engine.config.ecosystems_config_dict) == 0


@pytest.mark.asyncio
async def test_create_place(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.create,
        target="place",
        data={"place": "home", "coordinates": (0, 0)},
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update = events_handler._dispatcher.emit_store[1]["data"]
    assert data_update["uid"] == engine_uid
    gv.Place(**data_update["data"][0])
    coordinates = events_handler.engine.config.get_place("home")
    assert coordinates.longitude == 0
    assert coordinates.latitude == 0


@pytest.mark.asyncio
async def test_update_place_failure(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid},
        action=gv.CrudAction.update,
        target="place",
        data={"place": "home", "coordinates": (4, 2)},
    ).model_dump()

    await events_handler.on_crud(message)

    result_msg = events_handler._dispatcher.emit_store[0]["data"]
    assert result_msg["status"] == gv.Result.failure
    assert "No location named 'home' was found" in result_msg["message"]


@pytest.mark.asyncio
async def test_update_place(events_handler: Events):
    events_handler.engine.config.set_place("home", (0, 0))

    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid},
        action=gv.CrudAction.update,
        target="place",
        data={"place": "home", "coordinates": (4, 2)},
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update = events_handler._dispatcher.emit_store[1]["data"]
    assert data_update["uid"] == engine_uid
    gv.Place(**data_update["data"][0])
    coordinates = events_handler.engine.config.get_place("home")
    assert coordinates.latitude == 4
    assert coordinates.longitude == 2


@pytest.mark.asyncio
async def test_delete_place_failure(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.delete,
        target="place",
        data={"place": "home"},
    ).model_dump()

    await events_handler.on_crud(message)

    result_msg = events_handler._dispatcher.emit_store[0]["data"]
    assert result_msg["status"] == gv.Result.failure
    assert "No location named 'home' was found" in result_msg["message"]


@pytest.mark.asyncio
async def test_delete_place(events_handler: Events):
    events_handler.engine.config.set_place("home", (0, 0))

    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.delete,
        target="place",
        data={"place": "home"},
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update = events_handler._dispatcher.emit_store[1]["data"]
    assert data_update["uid"] == engine_uid
    assert len(data_update["data"]) == 0
    coordinates = events_handler.engine.config.get_place("home")
    assert coordinates is None


@pytest.mark.asyncio
async def test_update_chaos(events_handler: Events):
    frequency = 10
    duration = 5
    intensity = 1.10
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="chaos_config",
        data={"frequency": frequency, "duration": duration, "intensity": intensity},
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update = events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.ChaosParametersPayload(**data_update[0])
    assert verified.data.frequency == frequency
    assert verified.data.duration == duration
    assert verified.data.intensity == intensity


@pytest.mark.asyncio
async def test_update_management(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="management",
        data={"light": True},
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update = events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.ManagementConfigPayload(**data_update[0])
    assert verified.data.light is True


@pytest.mark.asyncio
async def test_update_time_parameters(events_handler: Events):
    day = time(8, 0)
    night = time(20, 0)
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="time_parameters",
        data={"day": day, "night": night},
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update: list[gv.LightDataPayloadDict] = \
        events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.LightDataPayload(**data_update[0])
    assert verified.data.morning_start == day
    assert verified.data.evening_end == night
    ecosystem_day = \
        events_handler.ecosystems[ecosystem_uid].config.nycthemeral_span_hours.day
    assert ecosystem_day == day
    ecosystem_night = \
        events_handler.ecosystems[ecosystem_uid].config.nycthemeral_span_hours.night
    assert ecosystem_night == night


@pytest.mark.asyncio
async def test_update_light_method(events_handler: Events):
    method = gv.LightMethod.elongate
    events_handler.engine.config.set_place("home", (0, 0))
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="light_method",
        data={"method": method},
    ).model_dump()

    await events_handler.on_crud(message)
    events_handler.dispatcher.emit_store.pop(0)  # TODO: Check why an event is inserted before
    assert_success(events_handler)

    data_update: list[gv.LightDataPayloadDict] = \
        events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.LightDataPayload(**data_update[0])
    assert verified.data.method == method
    assert events_handler.ecosystems[ecosystem_uid].lighting_method == method


@pytest.mark.asyncio
async def test_create_environment_parameter(events_handler: Events):
    parameter = gv.ClimateParameter.temperature
    day = 10.0
    night = 15.0
    hysteresis = 0.0
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.create,
        target="environment_parameter",
        data={
            "parameter": parameter,
            "day": day,
            "night": night,
            "hysteresis": hysteresis,
        },
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update: list[gv.EnvironmentConfigDict] = \
        events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.EnvironmentConfigPayload(**data_update[0])
    environment_parameter = verified.data.climate[0]
    assert environment_parameter.parameter == parameter
    assert environment_parameter.day == day
    assert environment_parameter.night == night
    assert environment_parameter.hysteresis == hysteresis


@pytest.mark.asyncio
async def test_update_environment_parameter_failure(events_handler: Events):
    parameter = gv.ClimateParameter.temperature
    day = 10.0
    night = 15.0
    hysteresis = None
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="environment_parameter",
        data={
            "parameter": parameter,
            "day": day,
            "night": night,
            "hysteresis": hysteresis,
        },
    ).model_dump()

    await events_handler.on_crud(message)

    result_msg = events_handler._dispatcher.emit_store[0]["data"]
    assert result_msg["status"] == gv.Result.failure
    assert "No climate parameter temperature was found" in result_msg["message"]


@pytest.mark.asyncio
async def test_update_environment_parameter(events_handler: Events):
    parameter = gv.ClimateParameter.temperature
    events_handler.ecosystems[ecosystem_uid].config.set_climate_parameter(
        parameter=parameter,
        day=42.0,
        night=21.0,
        hysteresis=3.14,
    )

    day = 10.0
    night = 15.0
    hysteresis = 0.0
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="environment_parameter",
        data={
            "parameter": parameter,
            "day": day,
            "night": night,
            "hysteresis": hysteresis,
        },
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update: list[gv.EnvironmentConfigDict] = \
        events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.EnvironmentConfigPayload(**data_update[0])
    environment_parameter = verified.data.climate[0]
    assert environment_parameter.parameter == parameter
    assert environment_parameter.day == day
    assert environment_parameter.night == night
    assert environment_parameter.hysteresis == hysteresis


@pytest.mark.asyncio
async def test_delete_environment_parameter_failure(events_handler: Events):
    parameter = gv.ClimateParameter.temperature
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.delete,
        target="environment_parameter",
        data={"parameter": parameter},
    ).model_dump()

    await events_handler.on_crud(message)

    result_msg = events_handler._dispatcher.emit_store[0]["data"]
    assert result_msg["status"] == gv.Result.failure
    assert "No climate parameter temperature was found" in result_msg["message"]


@pytest.mark.asyncio
async def test_delete_environment_parameter(events_handler: Events):
    parameter = gv.ClimateParameter.temperature
    events_handler.ecosystems[ecosystem_uid].config.set_climate_parameter(
        parameter=parameter,
        day=42.0,
        night=21.0,
        hysteresis=3.14,
    )

    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.delete,
        target="environment_parameter",
        data={"parameter": parameter},
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update: list[gv.EnvironmentConfigDict] = \
        events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.EnvironmentConfigPayload(**data_update[0])
    assert len(verified.data.climate) == 0


@pytest.mark.asyncio
async def test_create_hardware(events_handler: Events):
    events_handler.engine.config.ecosystems_config_dict[ecosystem_uid]["IO"] = {}
    valid_hardware_info = {
        **hardware_info,
        "model": "gpioSwitch",
        "address": "GPIO_11",  # Use a free address
    }
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.create,
        target="hardware",
        data=valid_hardware_info,
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update: list[gv.EnvironmentConfigDict] = \
        events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.HardwareConfigPayload(**data_update[0])
    hardware: gv.HardwareConfig = verified.data[0]
    assert hardware.name == valid_hardware_info["name"]
    assert hardware.address == valid_hardware_info["address"]
    assert hardware.type == valid_hardware_info["type"]
    assert hardware.level == valid_hardware_info["level"]


@pytest.mark.asyncio
async def test_update_hardware_failure(events_handler: Events):
    invalid_hardware_info = {
        "uid": "invalid_uid",
        "model": "gpioSwitch",
        "address": "GPIO_11",  # Use a free address
    }
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="hardware",
        data=invalid_hardware_info,
    ).model_dump()

    await events_handler.on_crud(message)

    result_msg = events_handler._dispatcher.emit_store[0]["data"]
    assert result_msg["status"] == gv.Result.failure
    assert "No hardware with uid 'invalid_uid' found" in result_msg["message"]


@pytest.mark.asyncio
async def test_update_hardware(events_handler: Events):
    valid_hardware_info = {
        "uid": hardware_uid,
        "model": "gpioSwitch",
        "address": "GPIO_11",  # Use a free address
    }
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="hardware",
        data=valid_hardware_info,
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update: list[gv.EnvironmentConfigDict] = \
        events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.HardwareConfigPayload(**data_update[0])
    hardware: gv.HardwareConfig = verified.data[2]
    assert hardware.address == valid_hardware_info["address"]
    assert hardware.model == valid_hardware_info["model"]


@pytest.mark.asyncio
async def test_delete_hardware_failure(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.delete,
        target="hardware",
        data={"uid": "invalid_uid"},
    ).model_dump()

    await events_handler.on_crud(message)

    result_msg = events_handler._dispatcher.emit_store[0]["data"]
    assert result_msg["status"] == gv.Result.failure
    assert "No hardware with uid 'invalid_uid' found" in result_msg["message"]


@pytest.mark.asyncio
async def test_delete_hardware(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.delete,
        target="hardware",
        data={"uid": hardware_uid},
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update: list[gv.EnvironmentConfigDict] = \
        events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.HardwareConfigPayload(**data_update[0])
    assert len(verified.data) == len(IO_dict) - 1
    assert hardware_uid not in [hardware.uid for hardware in verified.data]
