from datetime import time

import pytest

import gaia_validators as gv

from gaia.events import Events

from tests import data as test_data


hardware_dict = {
    test_data.light_uid: test_data.light_info,
}


def assert_success(
        events_handler: Events,
        caplog,
        expected_events_emitted: int = 2,
        crud_result_index: int = 0
) -> None:
    assert "was successfully treated" in caplog.text
    assert len(events_handler._dispatcher.emit_store) == expected_events_emitted
    emitted_msg: gv.RequestResultDict = \
        events_handler._dispatcher.emit_store[crud_result_index]["data"]
    assert emitted_msg["status"] == gv.Result.success


@pytest.mark.asyncio
class TestCRUDGeneral:
    async def test_wrong_engine_uid(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": "wrong_uid", "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.create,
            target="hardware",
            kwargs={},
        ).model_dump()

        await events_handler.on_crud(message)

        assert "Received a CRUD request intended to engine" in caplog.text

    async def test_missing_ecosystem_uid(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid},
            action=gv.CrudAction.create,
            target="hardware",
            kwargs={},
        ).model_dump()

        await events_handler.on_crud(message)

        error_msg = "Create hardware requires the 'ecosystem_uid' field to be set."
        assert error_msg in caplog.text
        emitted_msg: gv.RequestResultDict = events_handler._dispatcher.emit_store[0]["data"]
        assert emitted_msg["status"] == gv.Result.failure
        assert error_msg in emitted_msg["message"]

    async def test_routing_success(self, events_handler: Events, caplog: pytest.LogCaptureFixture):
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.create,
            target="ecosystem",
            kwargs={"ecosystem_name": "TestCrud"},
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)


@pytest.mark.asyncio
class TestCRUDEcosystem:
    async def test_create(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.create,
            target="ecosystem",
            kwargs={"ecosystem_name": "TestCrud"},
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update = events_handler._dispatcher.emit_store[1]["data"]
        assert len(data_update) == 2
        assert "TestCrud" in [ecosystem["data"]["name"] for ecosystem in data_update]
        assert len(events_handler.ecosystems) == 2
        assert "TestCrud" in [
            ecosystem.name
            for ecosystem in events_handler.ecosystems.values()
        ]

    async def test_update_failure(self, events_handler: Events):
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid},
            action=gv.CrudAction.update,
            target="ecosystem",
            kwargs={"ecosystem_id": "does_not_exists", "name": "NewName"},
        ).model_dump()

        await events_handler.on_crud(message)

        result_msg = events_handler._dispatcher.emit_store[0]["data"]
        assert result_msg["status"] == gv.Result.failure
        assert "Ecosystem with id 'does_not_exists' not found" in result_msg["message"]

    async def test_update(self, events_handler: Events, caplog: pytest.LogCaptureFixture):
        new_name = "NewName"
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid},
            action=gv.CrudAction.update,
            target="ecosystem",
            kwargs={"ecosystem_id": test_data.ecosystem_uid, "name": new_name},
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        ecosystem_config = \
            events_handler.engine.config.ecosystems_config_dict[test_data.ecosystem_uid]
        assert ecosystem_config["name"] == new_name

    async def test_delete_failure(self, events_handler: Events):
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid},
            action=gv.CrudAction.delete,
            target="ecosystem",
            kwargs={"ecosystem_id": "does_not_exists"},
        ).model_dump()

        await events_handler.on_crud(message)

        result_msg = events_handler._dispatcher.emit_store[0]["data"]
        assert result_msg["status"] == gv.Result.failure
        assert "Ecosystem with id 'does_not_exists' not found" in result_msg["message"]

    async def test_delete(self, events_handler: Events, caplog: pytest.LogCaptureFixture):
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.delete,
            target="ecosystem",
            kwargs={"ecosystem_id": test_data.ecosystem_uid},
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog, 1)

        assert len(events_handler.ecosystems) == 0
        assert len(events_handler.engine.ecosystems_started) == 0
        assert len(events_handler.engine.config.ecosystems_config_dict) == 0


@pytest.mark.asyncio
class TestCRUDPlace:
    async def test_create(self, events_handler: Events, caplog: pytest.LogCaptureFixture):
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.create,
            target="place",
            kwargs={"place": "home", "coordinates": (0, 0)},
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update = events_handler._dispatcher.emit_store[1]["data"]
        assert data_update["uid"] == test_data.engine_uid
        gv.Place(**data_update["data"][0])
        coordinates = events_handler.engine.config.get_place("home")
        assert coordinates.longitude == 0
        assert coordinates.latitude == 0

    async def test_update_failure(self, events_handler: Events):
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid},
            action=gv.CrudAction.update,
            target="place",
            kwargs={"place": "home", "coordinates": (4, 2)},
        ).model_dump()

        await events_handler.on_crud(message)

        result_msg = events_handler._dispatcher.emit_store[0]["data"]
        assert result_msg["status"] == gv.Result.failure
        assert "No location named 'home' was found" in result_msg["message"]

    async def test_update(self, events_handler: Events, caplog: pytest.LogCaptureFixture):
        events_handler.engine.config.set_place("home", (0, 0))

        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid},
            action=gv.CrudAction.update,
            target="place",
            kwargs={"place": "home", "coordinates": (4, 2)},
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update = events_handler._dispatcher.emit_store[1]["data"]
        assert data_update["uid"] == test_data.engine_uid
        gv.Place(**data_update["data"][0])
        coordinates = events_handler.engine.config.get_place("home")
        assert coordinates.latitude == 4
        assert coordinates.longitude == 2

    async def test_delete_failure(self, events_handler: Events):
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.delete,
            target="place",
            kwargs={"place": "home"},
        ).model_dump()

        await events_handler.on_crud(message)

        result_msg = events_handler._dispatcher.emit_store[0]["data"]
        assert result_msg["status"] == gv.Result.failure
        assert "No location named 'home' was found" in result_msg["message"]

    async def test_delete(self, events_handler: Events, caplog: pytest.LogCaptureFixture):
        events_handler.engine.config.set_place("home", (0, 0))

        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.delete,
            target="place",
            kwargs={"place": "home"},
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update = events_handler._dispatcher.emit_store[1]["data"]
        assert data_update["uid"] == test_data.engine_uid
        assert len(data_update["data"]) == 0
        coordinates = events_handler.engine.config.get_place("home")
        assert coordinates is None


@pytest.mark.asyncio
class TestCRUDChaos:
    async def test_update_chaos(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        frequency = 10
        duration = 5
        intensity = 1.10
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.update,
            target="chaos_config",
            kwargs={"frequency": frequency, "duration": duration, "intensity": intensity},
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update = events_handler._dispatcher.emit_store[1]["data"]
        verified = gv.ChaosParametersPayload(**data_update[0])
        assert verified.data.frequency == frequency
        assert verified.data.duration == duration
        assert verified.data.intensity == intensity


@pytest.mark.asyncio
class TestCRUDNychthemeralConfig:
    async def test_update_nycthemeral_config(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        events_handler.engine.config.set_place("home", (0, 0))

        span = gv.NycthemeralSpanMethod.mimic
        lighting = gv.LightMethod.elongate
        target = "home"
        day = time(8, 42)
        night = time(21, 0)
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.update,
            target="nycthemeral_config",
            kwargs={
                "span": span,
                "lighting": lighting,
                "target": target,
                "day": day,
                "night": night
            },
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog, 2, 0)  # crud_result and nycthemeral_info

        data_update = events_handler._dispatcher.emit_store[1]["data"]

        verified = gv.NycthemeralCycleInfoPayload(**data_update[0])
        assert verified.data.span == span
        assert verified.data.lighting == lighting
        assert verified.data.target == target
        assert verified.data.day == day
        assert verified.data.night == night


@pytest.mark.asyncio
class TestCRUDManagement:
    async def test_update_management(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.update,
            target="management",
            kwargs={"light": True},
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update = events_handler._dispatcher.emit_store[1]["data"]
        verified = gv.ManagementConfigPayload(**data_update[0])
        assert verified.data.light is True


@pytest.mark.asyncio
class TestCRUDClimateParameter:
    async def test_create(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        parameter = gv.ClimateParameter.temperature
        day = 10.0
        night = 15.0
        hysteresis = 0.0
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.create,
            target="climate_parameter",
            kwargs={
                "parameter": parameter,
                "day": day,
                "night": night,
                "hysteresis": hysteresis,
            },
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update: list[gv.ClimateConfigPayloadDict] = \
            events_handler._dispatcher.emit_store[1]["data"]
        verified = gv.ClimateConfigPayload(**data_update[0])

        for data in verified.data:
            # Some other parameters were already present in the config
            if data.parameter.name in test_data.climate_dict:
                continue
            assert data.parameter == parameter
            assert data.day == day
            assert data.night == night
            assert data.hysteresis == hysteresis

    async def test_update_failure(self, events_handler: Events):
        parameter = gv.ClimateParameter.light
        day = 100000.0
        night = 0.0
        hysteresis = None
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.update,
            target="climate_parameter",
            kwargs={
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

    async def test_update(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        parameter = gv.ClimateParameter.light
        events_handler.ecosystems[test_data.ecosystem_uid].config.set_climate_parameter(
            parameter=parameter,
            day=42.0,
            night=21.0,
            hysteresis=3.14,
        )

        day = 10.0
        night = 15.0
        hysteresis = 0.0
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.update,
            target="climate_parameter",
            kwargs={
                "parameter": parameter,
                "day": day,
                "night": night,
                "hysteresis": hysteresis,
            },
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update: list[gv.ClimateConfigPayloadDict] = \
            events_handler._dispatcher.emit_store[1]["data"]
        verified = gv.ClimateConfigPayload(**data_update[0])

        found = False
        for data in verified.data:
            # Some other parameters were already present in the config
            if data.parameter.name in test_data.climate_dict:
                continue
            found = True
            assert data.parameter == parameter
            assert data.day == day
            assert data.night == night
            assert data.hysteresis == hysteresis
        assert found

    async def test_delete_failure(self, events_handler: Events):
        parameter = gv.ClimateParameter.light
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.delete,
            target="climate_parameter",
            kwargs={"parameter": parameter},
        ).model_dump()

        await events_handler.on_crud(message)

        result_msg = events_handler._dispatcher.emit_store[0]["data"]
        assert result_msg["status"] == gv.Result.failure
        assert "No climate parameter light was found" in result_msg["message"]

    async def test_delete(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        parameter = gv.ClimateParameter.light
        events_handler.ecosystems[test_data.ecosystem_uid].config.set_climate_parameter(
            parameter=parameter,
            day=250000.0,
            night=0.0,
            hysteresis=10000,
        )

        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.delete,
            target="climate_parameter",
            kwargs={"parameter": parameter},
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update: list[gv.ClimateConfigPayloadDict] = \
            events_handler._dispatcher.emit_store[1]["data"]
        verified = gv.ClimateConfigPayload(**data_update[0])
        assert len(verified.data) == len(test_data.climate_dict)


@pytest.mark.asyncio
class TestCRUDWeatherEvent:
    async def test_create(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        parameter = gv.WeatherParameter.fog
        pattern = "0 7 * * *"
        duration = 30.0

        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.create,
            target="weather_event",
            kwargs={
                "parameter": parameter,
                "pattern": pattern,
                "duration": duration,
            },
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update: list[gv.WeatherConfigPayloadDict] = \
            events_handler._dispatcher.emit_store[1]["data"]
        verified_payload = gv.WeatherConfigPayload(**data_update[0])

        found = False
        for data in verified_payload.data:
            if data.parameter != parameter:
                continue
            found = True
            assert data.pattern == pattern
            assert data.duration == duration
        assert found

    async def test_update_failure(self, events_handler: Events):
        parameter = gv.WeatherParameter.fog
        pattern = "0 7 * * *"
        duration = 30.0

        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.update,
            target="weather_event",
            kwargs={
                "parameter": parameter,
                "pattern": pattern,
                "duration": duration,
            },
        ).model_dump()

        await events_handler.on_crud(message)

        result_msg = events_handler._dispatcher.emit_store[0]["data"]
        assert result_msg["status"] == gv.Result.failure
        assert "No weather parameter fog was found" in result_msg["message"]

    async def test_update(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        parameter = gv.WeatherParameter.rain
        pattern = "0 7 * * *"
        duration = 30.0

        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.update,
            target="weather_event",
            kwargs={
                "parameter": parameter,
                "pattern": pattern,
                "duration": duration,
            },
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update: list[gv.WeatherConfigPayloadDict] = \
            events_handler._dispatcher.emit_store[1]["data"]
        verified_payload = gv.WeatherConfigPayload(**data_update[0])

        found = False
        for data in verified_payload.data:
            if data.parameter != parameter:
                continue
            found = True
            assert data.pattern == pattern
            assert data.duration == duration
        assert found

    async def test_delete_failure(self, events_handler: Events):
        parameter = gv.WeatherParameter.fog
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.delete,
            target="weather_event",
            kwargs={"parameter": parameter},
        ).model_dump()

        await events_handler.on_crud(message)

        result_msg = events_handler._dispatcher.emit_store[0]["data"]
        assert result_msg["status"] == gv.Result.failure
        assert "No weather parameter fog was found" in result_msg["message"]

    async def test_delete(
            self,
            events_handler: Events,
            caplog: pytest.LogCaptureFixture,
    ):
        parameter = gv.WeatherParameter.fog
        events_handler.ecosystems[test_data.ecosystem_uid].config.set_weather_parameter(
            parameter=parameter,
            pattern="0 7 * * *",
            duration=30.0,
        )

        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.delete,
            target="weather_event",
            kwargs={"parameter": parameter},
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update: list[gv.WeatherConfigPayloadDict] = \
            events_handler._dispatcher.emit_store[1]["data"]
        verified_payload = gv.WeatherConfigPayload(**data_update[0])
        assert len(verified_payload.data) == len(test_data.weather_cfg)


@pytest.mark.asyncio
class TestCRUDHardware:
    async def test_create(self, events_handler: Events, caplog: pytest.LogCaptureFixture):
        events_handler.engine.config.ecosystems_config_dict[test_data.ecosystem_uid]["hardware"] = {}
        valid_hardware_info = {
            **test_data.humidifier_info,
            "model": "gpioSwitch",
            "address": "GPIO_11",  # Use a free address
        }
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.create,
            target="hardware",
            kwargs=valid_hardware_info,
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update: list[gv.HardwareConfigPayloadDict] = \
            events_handler._dispatcher.emit_store[1]["data"]
        verified = gv.HardwareConfigPayload(**data_update[0])
        hardware: gv.HardwareConfig = verified.data[0]
        assert hardware.name == valid_hardware_info["name"]
        assert hardware.address == valid_hardware_info["address"]
        assert hardware.type == valid_hardware_info["type"]
        assert hardware.level == valid_hardware_info["level"]

    @pytest.mark.parametrize("ecosystem_config", [{"hardware": hardware_dict}], indirect=True)
    async def test_update_failure(self, events_handler: Events):
        invalid_hardware_info = {
            "uid": "invalid_uid",
            "model": "gpioSwitch",
            "address": "GPIO_11",  # Use a free address
        }
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.update,
            target="hardware",
            kwargs=invalid_hardware_info,
        ).model_dump()

        await events_handler.on_crud(message)

        result_msg = events_handler._dispatcher.emit_store[0]["data"]
        assert result_msg["status"] == gv.Result.failure
        assert "No hardware with uid 'invalid_uid' found" in result_msg["message"]

    @pytest.mark.parametrize("ecosystem_config", [{"hardware": hardware_dict}], indirect=True)
    async def test_update(self, events_handler: Events, caplog: pytest.LogCaptureFixture):
        valid_hardware_info = {
            "uid": test_data.light_uid,
            "model": "gpioSwitch",
            "address": "GPIO_11",  # Use a free address
        }
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.update,
            target="hardware",
            kwargs=valid_hardware_info,
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update: list[gv.HardwareConfigPayloadDict] = \
            events_handler._dispatcher.emit_store[1]["data"]
        verified = gv.HardwareConfigPayload(**data_update[0])
        for hardware in verified.data:
            if hardware.uid != test_data.humidifier_uid:
                continue
            assert hardware.address == valid_hardware_info["address"]
            assert hardware.model == valid_hardware_info["model"]

    @pytest.mark.parametrize("ecosystem_config", [{"hardware": hardware_dict}], indirect=True)
    async def test_delete_failure(self, events_handler: Events):
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.delete,
            target="hardware",
            kwargs={"uid": "invalid_uid"},
        ).model_dump()

        await events_handler.on_crud(message)

        result_msg = events_handler._dispatcher.emit_store[0]["data"]
        assert result_msg["status"] == gv.Result.failure
        assert "No hardware with uid 'invalid_uid' found" in result_msg["message"]

    @pytest.mark.parametrize("ecosystem_config", [{"hardware": hardware_dict}], indirect=True)
    async def test_delete(self, events_handler: Events, caplog: pytest.LogCaptureFixture):
        message = gv.CrudPayloadDict = gv.CrudPayload(
            routing={"engine_uid": test_data.engine_uid, "ecosystem_uid": test_data.ecosystem_uid},
            action=gv.CrudAction.delete,
            target="hardware",
            kwargs={"uid": test_data.light_uid},
        ).model_dump()

        await events_handler.on_crud(message)

        assert_success(events_handler, caplog)

        data_update: list[gv.HardwareConfigPayloadDict] = \
            events_handler._dispatcher.emit_store[1]["data"]
        verified = gv.HardwareConfigPayload(**data_update[0])
        assert len(verified.data) == 0
        assert test_data.hardware_uid not in [hardware.uid for hardware in verified.data]
