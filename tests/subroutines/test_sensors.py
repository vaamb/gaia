from datetime import datetime, timezone

import pytest

import gaia_validators as gv

from gaia import Ecosystem
from gaia.subroutines import Sensors
from gaia.subroutines.sensors import ParameterLimits

from tests import data as test_data
from tests.utils import with_hardware


sensors_dict = {test_data.sensor_uid: test_data.sensor_info}


def _make_cache(*records: tuple[str, float]) -> gv.SensorsDataDict:
    return {
        "timestamp": datetime.now(timezone.utc),
        "records": [
            gv.SensorRecord(test_data.sensor_uid, measure, value)
            for measure, value in records
        ],
        "average": [],
        "alarms": [],
    }


@pytest.mark.asyncio
@with_hardware(sensors_dict)
class TestSensorsSubroutine:
    async def test_manageable(self, ecosystem: Ecosystem, sensors_subroutine: Sensors):
        assert sensors_subroutine.manageable

        for hardware_uid in sensors_subroutine.get_hardware_needed_uid():
            ecosystem.config.delete_hardware(hardware_uid)

        await ecosystem.refresh_hardware()

        assert not sensors_subroutine.manageable

    async def test_hardware_needed(self, sensors_subroutine: Sensors):
        uids = sensors_subroutine.get_hardware_needed_uid()
        assert uids == {test_data.sensor_uid}

    async def test_routine(self, sensors_subroutine: Sensors):
        # Rely on the correct implementation of virtualDHT22
        # Setup climate parameters to test alarms
        sensors_subroutine.config.set_management(gv.ManagementFlags.alarms, True)
        sensors_subroutine.config.set_climate_parameter(
            "temperature", **{"day": 42.0, "night": 42.0, "hysteresis": 1.0, "alarm": 0.5})

        # Enable the subroutine
        sensors_subroutine.enable()

        # Test start, routine, refresh and stop
        await sensors_subroutine.start()

        assert sensors_subroutine.sensors_data == gv.Empty()

        await sensors_subroutine.routine()

        assert isinstance(sensors_subroutine.sensors_data, gv.SensorsData)
        assert len(sensors_subroutine.sensors_data.records) > 0
        assert len(sensors_subroutine.sensors_data.average) > 0
        assert len(sensors_subroutine.sensors_data.alarms) > 0

        assert sensors_subroutine.ecosystem.sensors_data.records

        await sensors_subroutine.refresh()

        await sensors_subroutine.stop()

        sensors_subroutine.disable()


class TestGetAlarm:
    # Hysteresis band is [19.0, 21.0], `high` from a delta of 2.0 (alarm),
    #  `critical` from a delta of 3.0 (alarm + hysteresis)
    limits = ParameterLimits(target=20.0, hysteresis=1.0, alarm=2.0)

    @staticmethod
    def _record(value: float) -> gv.SensorRecord:
        return gv.SensorRecord(test_data.sensor_uid, "temperature", value)

    @pytest.mark.parametrize("value", [19.0, 20.0, 21.0])
    def test_within_hysteresis(self, value: float):
        assert Sensors._get_alarm(self._record(value), self.limits) is None

    @pytest.mark.parametrize(
        "value, position, delta, level",
        [
            (10.0, gv.Position.under, 9.0, gv.WarningLevel.critical),
            (17.0, gv.Position.under, 2.0, gv.WarningLevel.high),
            (18.0, gv.Position.under, 1.0, gv.WarningLevel.low),
            (22.0, gv.Position.above, 1.0, gv.WarningLevel.low),
            (23.0, gv.Position.above, 2.0, gv.WarningLevel.high),
            (23.5, gv.Position.above, 2.5, gv.WarningLevel.high),
            (24.0, gv.Position.above, 3.0, gv.WarningLevel.critical),
        ],
    )
    def test_outside_hysteresis(
            self,
            value: float,
            position: gv.Position,
            delta: float,
            level: gv.WarningLevel,
    ):
        alarm = Sensors._get_alarm(self._record(value), self.limits)

        assert alarm == gv.SensorAlarm(
            test_data.sensor_uid, "temperature", position, delta, level)


@pytest.mark.asyncio
@with_hardware(sensors_dict)
class TestAddSensorWarnings:
    async def test_unmonitored_measure_are_ignored(self, sensors_subroutine: Sensors):
        sensors_subroutine.config.set_climate_parameter(
            "temperature", **{"day": 20.0, "night": 20.0, "hysteresis": 1.0, "alarm": 2.0})
        cache = sensors_subroutine._add_sensor_warnings(
            _make_cache(("humidity", 100.0), ("temperature", 30.0)))

        assert [alarm.measure for alarm in cache["alarms"]] == ["temperature"]

    async def test_no_alarm_threshold(self, sensors_subroutine: Sensors):
        sensors_subroutine.config.set_climate_parameter(
            "temperature", **{"day": 20.0, "night": 20.0, "hysteresis": 1.0, "alarm": None})
        cache = sensors_subroutine._add_sensor_warnings(_make_cache(("temperature", 30.0)))

        assert cache["alarms"] == []

    async def test_zero_alarm_threshold(self, sensors_subroutine: Sensors):
        # An alarm threshold of 0.0 is valid and must not be treated as None
        sensors_subroutine.config.set_climate_parameter(
            "temperature", **{"day": 20.0, "night": 20.0, "hysteresis": 1.0, "alarm": 0.0})
        cache = sensors_subroutine._add_sensor_warnings(_make_cache(("temperature", 30.0)))

        assert len(cache["alarms"]) == 1

    @pytest.mark.parametrize(
        "is_day, expected",
        [(True, gv.Position.under), (False, gv.Position.above)],
    )
    async def test_day_night_target(
            self,
            sensors_subroutine: Sensors,
            monkeypatch: pytest.MonkeyPatch,
            is_day: bool,
            expected: gv.Position,
    ):
        sensors_subroutine.config.set_climate_parameter(
            "temperature", **{"day": 25.0, "night": 15.0, "hysteresis": 1.0, "alarm": 2.0})
        monkeypatch.setattr(sensors_subroutine.config, "is_day", lambda: is_day)
        cache = sensors_subroutine._add_sensor_warnings(_make_cache(("temperature", 20.0)))

        assert cache["alarms"][0].position == expected
