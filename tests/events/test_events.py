from asyncio import sleep
from collections import deque
from datetime import datetime, timezone
from math import isclose
from time import monotonic
from typing import cast
from unittest.mock import AsyncMock, patch
import uuid

from pydantic import ValidationError
import pytest
from sqlalchemy import delete

import gaia_validators as gv

from gaia import Ecosystem, EngineConfig
from gaia.config.from_files import PrivateConfigValidator
from gaia.database.models import ActuatorBuffer, SensorBuffer
from gaia.events import Events, validate_payload

from tests import data as test_data


hardware_dict = {
    test_data.light_uid: test_data.light_info,
}

camera_dict = {
    test_data.camera_uid: test_data.camera_info,
}


@pytest.mark.asyncio
class TestGeneral:
    async def test_validate_payload(self, events_handler: Events):
        # Test valid payload
        valid_data = {"uid": "test_uid", "data": {"health": True}}
        invalid_data = {"data": {"health": True}}  # missing 'uid'

        class EventTest:
            logger = events_handler.logger

            @validate_payload(gv.ManagementConfigPayload)
            async def test_validate(self, validated_data):
                pass

        event_test = EventTest()

        # Test empty data
        with pytest.raises(ValidationError):
            await event_test.test_validate(None)

        # Test invalid data
        with pytest.raises(ValidationError):
            await event_test.test_validate(invalid_data)

        # Test valid data
        await event_test.test_validate(valid_data)

    async def test_on_pong(self, events_handler: Events):
        await events_handler.on_pong()

        assert isclose(events_handler._last_heartbeat, monotonic(), abs_tol=0.01)

    async def test_filter_uids(self, events_handler: Events, ecosystem: Ecosystem):
        # Test with None (should return all uids)
        result = events_handler.filter_uids()
        assert len(result) == 1
        assert test_data.ecosystem_uid in result

        # Test with specific uid
        result = events_handler.filter_uids(test_data.ecosystem_uid)
        assert result == [test_data.ecosystem_uid]

        result = events_handler.filter_uids([test_data.ecosystem_uid])
        assert result == [test_data.ecosystem_uid]

        # Test with list of uids
        result = events_handler.filter_uids([test_data.ecosystem_uid, "nonexistent_uid"])
        assert result == [test_data.ecosystem_uid]  # Only existing uid should be returned

    async def test_get_payload(self, events_handler: Events, ecosystem: Ecosystem):
        # Test getting a valid payload
        payload_name = "management"
        test_data.ecosystem_uids = [test_data.ecosystem_uid]

        # Test with ecosystem payload
        result = events_handler.get_payload(payload_name, test_data.ecosystem_uids)
        assert isinstance(result, list)
        assert result[0]["uid"] == test_data.ecosystem_uid

        # Test with engine payload (places_list)
        result = events_handler.get_payload("places_list")
        assert isinstance(result, dict)
        assert "uid" in result
        assert "data" in result

    async def test_send_payload(self, events_handler: Events, ecosystem: Ecosystem):
        # Test sending a valid payload
        payload_name = "management"
        test_data.ecosystem_uids = [test_data.ecosystem_uid]

        # Mock the emit method to test if it's called correctly
        original_emit = events_handler.emit
        mock_emit = AsyncMock()
        events_handler.emit = mock_emit

        try:
            await events_handler.send_payload(payload_name, test_data.ecosystem_uids)

            # Check that emit was called with the correct parameters
            mock_emit.assert_called_once()
            event = mock_emit.call_args[0][0]
            assert event == payload_name
            payload = mock_emit.call_args[1]["data"]
            assert isinstance(payload, list)
            assert payload[0]["uid"] == test_data.ecosystem_uid
            mgt = ecosystem._payloads.management.model_dump()
            assert payload[0]["data"] == mgt

        finally:
            # Restore original emit method
            events_handler.emit = original_emit


@pytest.mark.asyncio
class TestOnEvent:
    async def test_on_connect(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        await events_handler.on_connect(None)

        assert "Connection to the message broker successful" in caplog.text

        response = events_handler._dispatcher.emit_store[0]

        assert response["event"] == "register_engine"
        assert response["data"]["engine_uid"] == test_data.engine_uid

        if events_handler._ping_task is not None:
            events_handler._ping_task.cancel()

    async def test_on_register(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        await events_handler.on_register()

        assert "Received registration request" in caplog.text

        response = events_handler._dispatcher.emit_store[0]

        assert response["event"] == "register_engine"
        assert response["data"]["engine_uid"] == test_data.engine_uid

    async def test_on_camera_token(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        test_token = "test_camera_token_123"

        await events_handler.on_camera_token(test_token)

        assert events_handler.camera_token == test_token

        assert "Received camera token from Ouranos" in caplog.text

    async def test_on_disconnect(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        await events_handler.on_disconnect()

        assert "Received a disconnection request" in caplog.text

    async def test_on_registration_ack_failure(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        uuid_str = uuid.uuid4().__str__()
        payload = gv.EngineRegistrationAck(
            host_uid=uuid_str,
            contract_version=0,
            status=gv.Result.failure,
        ).model_dump()
        await events_handler.on_registration_ack(payload)

        assert "registration acknowledgment for another dispatcher" in caplog.text

        host_uid = events_handler._dispatcher.host_uid.__str__()
        payload = gv.EngineRegistrationAck(
            host_uid=host_uid,
            contract_version=0,
            status=gv.Result.failure,
        ).model_dump()
        await events_handler.on_registration_ack(payload)

        assert "Registration refused: contract mismatch." in caplog.text

    @pytest.mark.parametrize("ecosystem_config", [{"hardware": hardware_dict}], indirect=True)
    async def test_on_registration_ack(
            self,
            engine_config: EngineConfig,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        engine_config._private_config = PrivateConfigValidator(**{
            "places": {
                test_data.place_name: gv.Coordinates(
                    latitude=test_data.place_latitude,
                    longitude=test_data.place_longitude,
                ),
            },
        }).model_dump()

        host_uid = events_handler._dispatcher.host_uid.__str__()
        payload = gv.EngineRegistrationAck(
            host_uid=host_uid,
            contract_version=0,
            status=gv.Result.success,
        ).model_dump()
        await events_handler.on_registration_ack(payload)

        assert "registration successful, sending initial ecosystems info" in caplog.text

        responses = deque(events_handler._dispatcher.emit_store)

        def get_uid(payload):
            return payload["data"][0]["uid"]

        def get_data(payload):
            return payload["data"][0]["data"]

        """Places payload"""
        # Places is currently the only engine-level event sent on registration
        payload = responses.popleft()
        assert payload["event"] == "places_list"
        assert payload["data"]["uid"] == test_data.engine_uid
        assert payload["data"]["data"][0]["name"] == test_data.place_name
        assert payload["data"]["data"][0]["coordinates"] == (test_data.place_latitude, test_data.place_longitude)

        """Base info payload"""
        payload = responses.popleft()
        assert payload["event"] == "base_info"
        assert get_uid(payload) == test_data.ecosystem_uid
        data = get_data(payload)
        assert data["engine_uid"] == test_data.engine_uid
        assert data["uid"] == test_data.ecosystem_uid
        assert data["name"] == test_data.ecosystem_name

        """Management payload"""
        payload = responses.popleft()
        assert payload["event"] == "management"
        assert get_uid(payload) == test_data.ecosystem_uid
        for management, value in get_data(payload).items():
            assert gv.ManagementFlags[management]
            assert value is False

        """Chaos parameters payload"""
        payload = responses.popleft()
        assert payload["event"] == "chaos_parameters"
        assert get_uid(payload) == test_data.ecosystem_uid

        """Nycthemeral data payload"""
        payload = responses.popleft()
        assert payload["event"] == "nycthemeral_info"
        assert get_uid(payload) == test_data.ecosystem_uid
        data = get_data(payload)
        assert data["lighting"] == test_data.lighting_method
        assert data["day"] == test_data.lighting_start
        assert data["night"] == test_data.lighting_stop

        """Climate payload"""
        def get_climate_cfg(
                payload_data: list[dict],
                climate_parameter: gv.ClimateParameter,
        ) -> gv.AnonymousClimateConfigDict:
            return [
                climate_cfg
                for climate_cfg in payload_data
                if climate_cfg["parameter"] == climate_parameter
            ][0]

        payload = responses.popleft()
        assert payload["event"] == "climate"
        assert get_uid(payload) == test_data.ecosystem_uid
        data = get_data(payload)
        temperature = get_climate_cfg(data, gv.ClimateParameter.temperature)
        assert temperature == {**test_data.temperature_cfg, "parameter": gv.ClimateParameter.temperature}
        humidity = get_climate_cfg(data, gv.ClimateParameter.humidity)
        assert humidity == {**test_data.humidity_cfg, "parameter": gv.ClimateParameter.humidity}
        wind = get_climate_cfg(data, gv.ClimateParameter.wind)
        assert wind == {**test_data.wind_cfg, "parameter": gv.ClimateParameter.wind}

        """Weather payload"""
        payload = responses.popleft()
        assert payload["event"] == "weather"
        assert get_uid(payload) == test_data.ecosystem_uid
        data = get_data(payload)
        assert data[0] == {**test_data.rain_cfg, "parameter": gv.WeatherParameter.rain}

        """Hardware payload"""
        payload = responses.popleft()
        assert payload["event"] == "hardware"
        assert get_uid(payload) == test_data.ecosystem_uid
        # The only injected hardware parametrized is the light switch
        data = get_data(payload)
        assert len(data) == 1
        h: gv.HardwareConfig = data[0]
        assert h["uid"] in test_data.IO_dict.keys()
        assert h["name"] == test_data.light_info["name"]
        assert h["address"] == test_data.light_info["address"]
        assert h["model"] == test_data.light_info["model"]
        assert h["type"] == test_data.light_info["type"]
        assert h["level"] == test_data.light_info["level"]

        """Plants payload"""
        payload = responses.popleft()
        assert payload["event"] == "plants"
        assert get_uid(payload) == test_data.ecosystem_uid

        """Actuators state payload"""
        payload = responses.popleft()
        assert payload["event"] == "actuators_data"
        assert get_uid(payload) == test_data.ecosystem_uid
        for actuator_record in get_data(payload):
            actuator_record: gv.ActuatorStateRecord
            actuator_type = gv.HardwareType(actuator_record[0])
            assert actuator_type & gv.HardwareType.actuator
            assert actuator_record[1] == actuator_type.name
            assert actuator_record[2] is False
            assert actuator_record[3] == gv.ActuatorMode.automatic
            assert actuator_record[4] is False

        """Initialization finished"""
        payload = responses.popleft()
        assert payload["event"] == "initialization_data_sent"

        assert len(responses) == 0

    async def test_on_initialized_ack(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        await events_handler.on_initialization_ack(None)

        assert "Ouranos successfully received ecosystems info" in caplog.text

        await events_handler.on_initialization_ack(["base_info"])

        assert "Non-received info: base_info" in caplog.text

    @pytest.mark.parametrize("ecosystem_config", [{"hardware": hardware_dict}], indirect=True)
    async def test_on_turn_actuator(
            self,
            events_handler: Events,
            ecosystem: Ecosystem,
            caplog: pytest.LogCaptureFixture,
    ):
        await ecosystem.enable_subroutine("light")
        await ecosystem.start_subroutine("light")

        caplog.clear()

        actuator_name: str = cast(str, gv.HardwareType.light.name)
        handler = ecosystem.actuator_hub.get_handler(actuator_name)
        mode = handler.mode
        current_state = handler.status
        countdown = 0.5

        payload_mode = gv.ActuatorModePayload.on
        turn_actuator_payload = gv.TurnActuatorPayloadDict(**{
            "ecosystem_uid": test_data.ecosystem_uid,
            "actuator": gv.HardwareType[actuator_name],
            "mode": payload_mode,
            "countdown": countdown,
        })
        await events_handler.on_turn_actuator(turn_actuator_payload)

        assert "Received 'turn_actuator' event" in caplog.text
        assert actuator_name in caplog.text
        assert payload_mode.name in caplog.text

        handler = ecosystem.actuator_hub.get_handler(actuator_name)
        assert handler.status is current_state
        assert handler.mode is mode
        assert isclose(handler.countdown, countdown, abs_tol=0.01)

        # It sometimes takes more than 0.01s to reset the countdown
        await sleep(countdown + 0.03)

        assert handler.status is True
        assert handler.mode is gv.ActuatorMode.manual
        assert handler.countdown is None

        await ecosystem.disable_subroutine("light")
        await ecosystem.stop_subroutine("light")

    async def test_on_change_management(self, events_handler: Events, ecosystem: Ecosystem):
        assert not ecosystem.config.get_management("camera")

        await events_handler.on_change_management({
            "uid": test_data.ecosystem_uid,
            "data": {"camera": True},
        })

        assert ecosystem.config.get_management("camera")


@pytest.mark.asyncio
class TestSendEvent:
    async def test_send_buffered_data_and_ack(
            self,
            events_handler: Events,
            ecosystem: Ecosystem,
    ):
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
                    ecosystem_uid=test_data.ecosystem_uid,
                    sensor_uid=test_data.sensor_uid,
                    measure="temperature",
                    timestamp=now,
                    value="21.0",
                )
            )
            session.add(
                ActuatorBuffer(
                    ecosystem_uid=test_data.ecosystem_uid,
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
        assert sensors_data["data"]["data"][0][0] == test_data.ecosystem_uid
        assert sensors_data["data"]["data"][0][1] == test_data.sensor_uid
        sensors_data_uuid = sensors_data["data"]["uuid"]

        actuators_data = events_handler.dispatcher.emit_store[1]
        assert actuators_data["event"] == "buffered_actuators_data"
        assert actuators_data["data"]["data"][0][0] == test_data.ecosystem_uid
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

    @pytest.mark.parametrize("ecosystem_config", [{"hardware": camera_dict}], indirect=True)
    async def test_send_picture_arrays(self, events_handler: Events, ecosystem: Ecosystem):
        pictures_subroutine = ecosystem.get_subroutine("pictures")
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


@pytest.mark.asyncio
class TestUpload:
    async def test_upload_picture_arrays_no_token(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        events_handler.camera_token = None

        await events_handler.upload_picture_arrays()

        assert "No camera token found, cannot send picture arrays" in caplog.text

    @pytest.mark.parametrize("ecosystem_config", [{"hardware": camera_dict}], indirect=True)
    @patch("aiohttp.ClientSession")
    async def test_upload_picture_arrays(
            self,
            mock_session,
            events_handler: Events,
            ecosystem: Ecosystem,
    ):
        # Setup test data
        test_token = "test_token_123"
        events_handler.camera_token = test_token

        # Mock the response
        mock_response = AsyncMock()
        mock_response.json.return_value = {"status": "success"}
        mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response

        # Enable camera and take a picture
        pictures_subroutine = ecosystem.get_subroutine("pictures")
        pictures_subroutine.config.set_management("camera", True)
        pictures_subroutine.enable()
        await pictures_subroutine.start()
        await pictures_subroutine.routine()

        # Test the upload
        await events_handler.upload_picture_arrays()

        # Verify the request was made with the correct token
        assert mock_session.call_args[1]["headers"]["token"] == test_token
