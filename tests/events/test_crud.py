from datetime import time

import pytest

import gaia_validators as gv
from gaia_validators import safe_enum_from_name

from gaia.events import Events as Events_

from ..data import (
    climate_dict, ecosystem_uid, engine_uid, hardware_info, hardware_uid, IO_dict)
from ..utils import get_logs_content, MockDispatcher


class Events(Events_):
    _dispatcher: MockDispatcher


def assert_success(
        events_handler: Events,
        expected_events_emitted: int = 2,
        crud_result_index: int = 0
) -> None:
    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "was successfully treated" in logs
    assert len(events_handler._dispatcher.emit_store) == expected_events_emitted
    emitted_msg: gv.RequestResultDict = \
        events_handler._dispatcher.emit_store[crud_result_index]["data"]
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
async def test_update_nycthemeral_config(events_handler: Events):
    events_handler.engine.config.set_place("home", (0, 0))

    span = gv.NycthemeralSpanMethod.mimic
    lighting = gv.LightMethod.elongate
    target = "home"
    day = time(8, 42)
    night = time(21, 0)
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="nycthemeral_config",
        data={
            "span": span,
            "lighting": lighting,
            "target": target,
            "day": day,
            "night": night
        },
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler, 3, 1)  # updated light_data, crud_result, and nycthemeral_cycle

    data_update = events_handler._dispatcher.emit_store[2]["data"]

    verified = gv.NycthemeralCycleInfoPayload(**data_update[0])
    assert verified.data.span == span
    assert verified.data.lighting == lighting
    assert verified.data.target == target
    assert verified.data.day == day
    assert verified.data.night == night


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
async def test_create_climate_parameter(events_handler: Events):
    parameter = gv.ClimateParameter.temperature
    day = 10.0
    night = 15.0
    hysteresis = 0.0
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.create,
        target="climate_parameter",
        data={
            "parameter": parameter,
            "day": day,
            "night": night,
            "hysteresis": hysteresis,
        },
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update: list[gv.ClimateConfigPayloadDict] = \
        events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.ClimateConfigPayload(**data_update[0])

    for data in verified.data:
        # Some other parameters were already present in the config
        if data.parameter.name in climate_dict:
            continue
        assert data.parameter == parameter
        assert data.day == day
        assert data.night == night
        assert data.hysteresis == hysteresis


@pytest.mark.asyncio
async def test_update_climate_parameter_failure(events_handler: Events):
    parameter = gv.ClimateParameter.light
    day = 100000.0
    night = 0.0
    hysteresis = None
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="climate_parameter",
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
    assert "No climate parameter light was found" in result_msg["message"]


@pytest.mark.asyncio
async def test_update_climate_parameter(events_handler: Events):
    parameter = gv.ClimateParameter.light
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
        target="climate_parameter",
        data={
            "parameter": parameter,
            "day": day,
            "night": night,
            "hysteresis": hysteresis,
        },
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update: list[gv.ClimateConfigPayloadDict] = \
        events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.ClimateConfigPayload(**data_update[0])

    for data in verified.data:
        # Some other parameters were already present in the config
        if data.parameter.name in climate_dict:
            continue
        assert data.parameter == parameter
        assert data.day == day
        assert data.night == night
        assert data.hysteresis == hysteresis


@pytest.mark.asyncio
async def test_delete_climate_parameter_failure(events_handler: Events):
    parameter = gv.ClimateParameter.light
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.delete,
        target="climate_parameter",
        data={"parameter": parameter},
    ).model_dump()

    await events_handler.on_crud(message)

    result_msg = events_handler._dispatcher.emit_store[0]["data"]
    assert result_msg["status"] == gv.Result.failure
    assert "No climate parameter light was found" in result_msg["message"]


@pytest.mark.asyncio
async def test_delete_climate_parameter(events_handler: Events):
    parameter = gv.ClimateParameter.light
    events_handler.ecosystems[ecosystem_uid].config.set_climate_parameter(
        parameter=parameter,
        day=250000.0,
        night=0.0,
        hysteresis=10000,
    )

    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.delete,
        target="climate_parameter",
        data={"parameter": parameter},
    ).model_dump()

    await events_handler.on_crud(message)

    assert_success(events_handler)

    data_update: list[gv.ClimateConfigPayloadDict] = \
        events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.ClimateConfigPayload(**data_update[0])
    assert len(verified.data) == len(climate_dict)


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

    data_update: list[gv.HardwareConfigPayloadDict] = \
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

    data_update: list[gv.HardwareConfigPayloadDict] = \
        events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.HardwareConfigPayload(**data_update[0])
    for hardware in verified.data:
        if hardware.uid != hardware_uid:
            continue
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

    data_update: list[gv.HardwareConfigPayloadDict] = \
        events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.HardwareConfigPayload(**data_update[0])
    assert len(verified.data) == len(IO_dict) - 1
    assert hardware_uid not in [hardware.uid for hardware in verified.data]
