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


def test_update_time_parameters(events_handler: Events):
    message = gv.CrudPayloadDict = gv.CrudPayload(
        routing={"engine_uid": engine_uid, "ecosystem_uid": ecosystem_uid},
        action=gv.CrudAction.update,
        target="time_parameters",
        data={"day": time(8, 0), "night": time(20, 0)},
    ).model_dump()

    events_handler.on_crud(message)

    with get_logs_content(events_handler.engine.config.logs_dir / "gaia.log") as logs:
        assert "was successfully treated" in logs
    emitted_msg: gv.RequestResultDict = events_handler._dispatcher.emit_store[0]["data"]
    assert emitted_msg["status"] == gv.Result.success

    updated_data: list[gv.LightDataPayloadDict] = events_handler._dispatcher.emit_store[1]["data"]
    gv.LightDataPayload(**updated_data[0])
