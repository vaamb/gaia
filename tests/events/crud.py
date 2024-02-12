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
