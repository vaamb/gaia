from datetime import time

import gaia_validators as gv

from gaia.events import Events

from ..data import ecosystem_uid, engine_uid
from ..utils import get_logs_content


def test_wrong_engine_uid(events_handler: Events):
    # Wrong engine_uid
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": "wrong_uid", "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.create,
        target="hardware",
        data={},
    ).model_dump()

    events_handler.on_crud(message)

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "Received a CRUD request intended to engine" in logs


def test_missing_ecosystem_uid(events_handler: Events):
    # Missing ecosystem_uid
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid},
        action=gv.CrudAction.create,
        target="hardware",
        data={},
    ).model_dump()

    events_handler.on_crud(message)

    error_msg = "Create hardware requires the 'ecosystem_uid' field to be set."
    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert error_msg in logs
    emitted_msg: gv.RequestResultDict = events_handler._dispatcher.emit_store[0]["data"]
    assert emitted_msg["status"] == gv.Result.failure
    assert error_msg in emitted_msg["message"]


def test_success(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.create,
        target="ecosystem",
        data={"ecosystem_name": "TestCrud"},
    ).model_dump()

    events_handler.on_crud(message)

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "was successfully treated" in logs
    emitted_msg: gv.RequestResultDict = events_handler._dispatcher.emit_store[0]["data"]
    assert emitted_msg["status"] == gv.Result.success


def test_create_ecosystem(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.create,
        target="ecosystem",
        data={"ecosystem_name": "TestCrud"},
    ).model_dump()

    events_handler.on_crud(message)

    data_update = events_handler._dispatcher.emit_store[1]["data"]
    assert len(data_update) == 2
    assert "TestCrud" in [ecosystem["data"]["name"] for ecosystem in data_update]
    assert len(events_handler.ecosystems) == 2
    assert "TestCrud" in [ecosystem.name for ecosystem in events_handler.ecosystems.values()]


def test_delete_ecosystem(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.delete,
        target="ecosystem",
        data={"ecosystem_id": ecosystem_uid},
    ).model_dump()

    events_handler.on_crud(message)

    assert len(events_handler.ecosystems) == 0
    assert len(events_handler.engine.ecosystems_started) == 0
    assert len(events_handler.engine.config.ecosystems_config_dict) == 0


def test_create_place(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.create,
        target="place",
        data={"place": "home", "coordinates": (0, 0)},
    ).model_dump()

    events_handler.on_crud(message)

    data_update = events_handler._dispatcher.emit_store[1]["data"]
    assert data_update["uid"] == engine_uid
    gv.Place(**data_update["data"][0])
    coordinates = events_handler.engine.config.get_place("home")
    assert coordinates.longitude == 0
    assert coordinates.latitude == 0


def test_update_place(events_handler: Events):
    events_handler.engine.config.set_place("home", (0, 0))

    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="place",
        data={"place": "home", "coordinates": (4, 2)},
    ).model_dump()

    events_handler.on_crud(message)

    data_update = events_handler._dispatcher.emit_store[1]["data"]
    assert data_update["uid"] == engine_uid
    gv.Place(**data_update["data"][0])
    coordinates = events_handler.engine.config.get_place("home")
    assert coordinates.latitude == 4
    assert coordinates.longitude == 2


def test_delete_place(events_handler: Events):
    events_handler.engine.config.set_place("home", (0, 0))

    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.delete,
        target="place",
        data={"place": "home"},
    ).model_dump()

    events_handler.on_crud(message)

    data_update = events_handler._dispatcher.emit_store[1]["data"]
    assert data_update["uid"] == engine_uid
    assert len(data_update["data"]) == 0

    coordinates = events_handler.engine.config.get_place("home")
    assert coordinates is None


def test_update_chaos(events_handler: Events):
    frequency = 10
    duration = 5
    intensity = 1.10
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="chaos_config",
        data={"frequency": frequency, "duration": duration, "intensity": intensity},
    ).model_dump()

    events_handler.on_crud(message)

    data_update = events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.ChaosParametersPayload(**data_update[0])
    assert  verified.data.frequency == frequency
    assert verified.data.duration == duration
    assert verified.data.intensity == intensity


def test_update_management(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="management",
        data={"light": True},
    ).model_dump()

    events_handler.on_crud(message)

    data_update = events_handler._dispatcher.emit_store[1]["data"]
    verified = gv.ManagementConfigPayload(**data_update[0])
    assert verified.data.light is True


def test_update_time_parameters(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="time_parameters",
        data={"day": time(8, 0), "night": time(20, 0)},
    ).model_dump()

    events_handler.on_crud(message)

    data_update: list[gv.LightDataPayloadDict] = events_handler._dispatcher.emit_store[1]["data"]
    gv.LightDataPayload(**data_update[0])
