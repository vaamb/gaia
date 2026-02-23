from __future__ import annotations

from datetime import datetime, time, timedelta
import logging
import math
from time import monotonic
import typing

import gaia_validators as gv

from gaia.utils import (
    get_absolute_humidity, get_relative_humidity, SingletonMeta,
    temperature_converter)


if typing.TYPE_CHECKING:
    from gaia import Ecosystem, Engine


SUNRISE = time(7, 0)
SUNSET = time(19, 0)


# 1 watt = 1 joule / sec
class VirtualWorld(metaclass=SingletonMeta):
    def __init__(
            self,
            engine: Engine,
            equinox_sun_times: tuple[time, time] = (SUNRISE, SUNSET),
            yearly_amp_sun_times: timedelta = timedelta(hours=2),
            # Actually half amp
            avg_temperature: float = 12.5,  # 12.5
            daily_amp_temperature: float = 4.0,  # 4.0
            yearly_amp_temperature: float = 0.0,  # 7.5
            avg_humidity: float = 40.0,  # 50.0
            amp_humidity: float = 10.0,  # 10.0
            avg_midday_light: float = 75000.0,
            yearly_amp_light: float = 25000.0,
            **kwargs,
    ) -> None:
        self.logger: logging.Logger = logging.getLogger("virtual.world")
        self._engine: Engine = engine
        self._sunrise: time = equinox_sun_times[0]
        self._sunset: time = equinox_sun_times[1]
        self._yearly_amp_sun_times: timedelta = yearly_amp_sun_times

        self._params = {
            "temperature": {
                "avg": avg_temperature,
                "daily_amp": daily_amp_temperature,
                "yearly_amp": yearly_amp_temperature,
            },
            "humidity": {
                "avg": avg_humidity,
                "amp": amp_humidity,
            },
            "light": {
                "max": avg_midday_light,
                "yearly_amp": yearly_amp_light,
            },
        }
        self._temperature: float = avg_temperature
        self._humidity: float = avg_humidity
        self._light: float = avg_midday_light
        self._dt: datetime | None = None
        self._last_update: float | None = None

    def get_measures(
            self,
            time_now: datetime | None = None,
    ) -> tuple[float, float, float]:
        mono_clock = monotonic()
        if (
            not self._last_update
            or mono_clock - self._last_update > 5.0
        ):
            self._compute_changes(time_now)
            self._last_update = mono_clock
        return self.temperature, self.humidity, self.light

    def _compute_changes(self, time_now: datetime | None = None) -> None:
        if time_now:
            now = time_now
        else:
            now = datetime.now().astimezone()
        self._dt = now
        yday = now.timetuple().tm_yday
        day_since_spring = (yday - 80) % 365
        season_factor = math.sin((day_since_spring / 365) * 2 * math.pi)

        base_temperature = (
            self._params["temperature"]["avg"] +
            self._params["temperature"]["yearly_amp"] * season_factor
        )
        base_light = (
            self._params["light"]["max"] +
            self._params["light"]["yearly_amp"] * season_factor
        )

        sunrise = datetime.combine(now.date(), self._sunrise).astimezone()
        sunrise = sunrise - (self._yearly_amp_sun_times * season_factor)
        sunset = datetime.combine(now.date(), self._sunset).astimezone()
        sunset = sunset + (self._yearly_amp_sun_times * season_factor)
        daytime = (sunset - sunrise).seconds
        nighttime = 24 * 3600 - daytime

        if sunrise <= now <= sunset:
            seconds_since_sunrise = (now - sunrise).seconds
            day_factor = math.sin((seconds_since_sunrise / daytime) * math.pi)
            light = base_light * day_factor
            self._light = int(light)

        else:
            # If after midnight
            if now < sunset:
                sunset = sunset - timedelta(days=1)
            seconds_since_sunset = (now - sunset).seconds
            day_factor = -math.sin((seconds_since_sunset / nighttime) * math.pi)
            light = 0
            self._light = int(light)

        temperature = (
            base_temperature
            + self._params["temperature"]["yearly_amp"] * season_factor
            + self._params["temperature"]["daily_amp"] * day_factor
        )
        self._temperature = round(temperature, 2)
        humidity = (
            self._params["humidity"]["avg"]
            - self._params["humidity"]["amp"] * day_factor
        )
        self._humidity = round(humidity, 2)

        self.logger.debug(
            f"Temperature: {self.temperature:2.2f} - humidity: {self.humidity:3.2f} - "
            f"light: {self.light:.1f}"
        )

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def temperature(self) -> float:
        if self._temperature is None:
            raise RuntimeError(
                "VirtualWorld must be started to get environmental values"
            )
        return self._temperature

    @property
    def humidity(self) -> float:
        if self._humidity is None:
            raise RuntimeError(
                "VirtualWorld must be started to get environmental values"
            )
        return self._humidity

    @property
    def light(self) -> float:
        if self._light is None:
            raise RuntimeError(
                "VirtualWorld must be started to get environmental values"
            )
        return self._light


class VirtualEcosystem:
    time_between_measures = 5

    AIR_HEAT_CAPACITY = 1  # kj/kg/K
    AIR_DENSITY = 1.225  # kg/m3
    WATER_HEAT_CAPACITY = 4.184  # kj/kg/K
    INSULATION_U_VAL = 3.5  # W/m2K -> Assumes a ~ 5mm polyacrylate sheet

    def __init__(
            self,
            ecosystem: Ecosystem,
            virtual_world: VirtualWorld,
            dimension: tuple[float, float, float] = (0.5, 0.5, 1.0),
            water_volume: float = 15.0,  # in liter
            max_heater_output: float = 100.0,  # max heater output in watt
            max_humidifier_output: float = 0.03,  # max humidifier output in g/water per second
            max_light_output: float = 30000.0,  # max light output in lux
            start: bool = False,
            **kwargs,
    ) -> None:
        assert len(dimension) == 3
        self._ecosystem: Ecosystem = ecosystem
        self.logger: logging.Logger = logging.getLogger(f"virtual.ecosystem.{ecosystem.uid}")
        self._virtual_world: VirtualWorld = virtual_world
        self._volume = dimension[0] * dimension[1] * dimension[2]
        # Assumes only loss through walls
        self._exchange_surface: float = (
                2 * dimension[2] * (dimension[0] + dimension[1])
        )  # in W/K
        self._water_volume: float = water_volume

        self._light: float | None = None

        self._max_heater_output: float = max_heater_output
        self._max_humidifier_output: float = max_humidifier_output
        self._max_light_output: float = max_light_output

        self._heat_quantity: float | None = None       # Total heat in the enclosure, in joules
        self._hybrid_capacity: float | None = None     # A mix between air and water heat capacity * volume, in j/K
        self._humidity_quantity: float | None = None   # Total humidity in the enclosure, in grams

        self._start_time: float | None = None
        self._last_update: float | None = None

        if start:
            self.start()

    @property
    def virtual_world(self) -> VirtualWorld:
        return self._virtual_world

    @property
    def ecosystem(self) -> Ecosystem:
        return self._ecosystem

    @property
    def volume(self) -> float:
        return self._volume

    @property
    def exchange_surface(self) -> float:
        return self._exchange_surface

    @property
    def heat_loss_coef(self) -> float:
        return self.exchange_surface * self.INSULATION_U_VAL

    @property
    def temperature(self) -> float:
        if not self.status:
            raise RuntimeError(
                "VirtualWorld must be started to get environmental values"
            )
        k_temperature = self._heat_quantity / self._hybrid_capacity
        return temperature_converter(k_temperature, "k", "c")

    @property
    def absolute_humidity(self) -> float:
        if not self.status:
            raise RuntimeError(
                "VirtualWorld must be started to get environmental values"
            )
        return self._humidity_quantity / self.volume

    @property
    def humidity(self) -> float:
        if not self.status:
            raise RuntimeError(
                "VirtualWorld must be started to get environmental values"
            )
        return get_relative_humidity(self.temperature, self.absolute_humidity)

    @property
    def light(self) -> float:
        if not self.get_actuator_status(gv.HardwareType.light):
            raise RuntimeError(
                "VirtualWorld must be started to get environmental values"
            )
        return self._light

    lux = light

    @property
    def uptime(self) -> float:
        return monotonic() - self._start_time

    @property
    def status(self) -> bool:
        return self._start_time is not None

    def get_actuator_status(self, actuator_group: str) -> bool:
        if actuator_group in self.ecosystem.actuator_hub.actuator_handlers:
            return self.ecosystem.actuator_hub.get_handler(actuator_group).status
        return False

    def get_actuator_level(self, actuator_group: str) -> float | None:
        if actuator_group in self.ecosystem.actuator_hub.actuator_handlers:
            return self.ecosystem.actuator_hub.get_handler(actuator_group).level
        return None

    def measure(self, now: float | None = None) -> None:
        if not self._start_time:
            raise RuntimeError(
                "The virtualEcosystem needs to be started " "before computing measures"
            )
        now = now or monotonic()
        if (
            self._last_update is None
            or (now - self._last_update) > self.time_between_measures
        ):
            self._measure(now)
            self._last_update = now

    def _measure(self, now: float) -> None:
        if not self._start_time:
            raise RuntimeError(
                "The virtualEcosystem needs to be started " "before computing measures"
            )

        if self._last_update is None:
            d_sec = 0.1
        else:
            d_sec = now - self._last_update
        out_temp, out_hum, out_light = self.virtual_world.get_measures()

        def get_corrected_level(actuator_group: str) -> float:
            level = self.get_actuator_level(actuator_group)
            if level is None:
                level = 100.0
            return level

        actuator_couples = self.ecosystem.config.get_actuator_couples()

        # New heat quantity
        d_temp = self.temperature - out_temp
        heat_quantity = self._heat_quantity
        heat_loss = self.heat_loss_coef * d_sec * d_temp
        heat_quantity -= heat_loss
        temperature_couple: gv.ActuatorCouple = actuator_couples[gv.ClimateParameter.temperature]
        temp_inc = temperature_couple.increase
        if temp_inc and self.get_actuator_status(temp_inc):
            level = get_corrected_level(temp_inc)
            heater_output = self._max_heater_output * d_sec * level / 100
            heat_quantity += heater_output
        temp_dec = temperature_couple.decrease
        if temp_dec and self.get_actuator_status(temp_dec):
            level = get_corrected_level(temp_dec)
            cooler_output = self._max_heater_output * 0.60 * d_sec * level / 100
            heat_quantity -= cooler_output
        self._heat_quantity = heat_quantity

        # Humidity calculation
        d_hum = self.absolute_humidity - get_absolute_humidity(out_temp, out_hum)
        humidity_quantity = self._humidity_quantity
        humidity_loss = d_hum * d_sec / 10000  # Pretty much a random factor
        humidity_quantity -= humidity_loss
        humidity_couple: gv.ActuatorCouple = actuator_couples[gv.ClimateParameter.humidity]
        hum_inc = humidity_couple.increase
        if hum_inc and self.get_actuator_status(hum_inc):
            level = get_corrected_level(hum_inc)
            humidifier_output = self._max_humidifier_output * d_sec * level / 100
            humidity_quantity += humidifier_output
        hum_dec = humidity_couple.decrease
        if hum_dec and self.get_actuator_status(hum_dec):
            level = get_corrected_level(hum_dec)
            dehumidifier_output = \
                self._max_humidifier_output * 0.50 * d_sec * level / 100
            humidity_quantity -= dehumidifier_output
        self._humidity_quantity = humidity_quantity

        # Light calculation
        self._light = out_light
        light_couple: gv.ActuatorCouple = actuator_couples[gv.ClimateParameter.light]
        light_inc = light_couple.increase
        if light_inc and self.get_actuator_status(light_inc):
            level = get_corrected_level(light_inc)
            self._light += self._max_light_output * level / 100
        self._last_update = now
        self.logger.debug(
            f"Temperature: {self.temperature:2.2f} - humidity: {self.humidity:3.2f} - "
            f"light: {self.light:.1f}"
        )

    def reset(self) -> None:
        air_mass = self.volume * self.AIR_DENSITY
        air_heat_capacity = air_mass * self.AIR_HEAT_CAPACITY * 1000  # in j/K

        water_mass = self._water_volume
        water_heat_capacity = water_mass * self.WATER_HEAT_CAPACITY * 1000  # in j/K

        self._hybrid_capacity = air_heat_capacity + water_heat_capacity

        out_temp, out_hum, out_light = self.virtual_world.get_measures()
        k_temperature = temperature_converter(out_temp, "c", "k")

        self._heat_quantity = self._hybrid_capacity * k_temperature
        out_abs_hum = get_absolute_humidity(out_temp, out_hum)
        self._humidity_quantity = out_abs_hum * self.volume

        self._light = out_light

        self._start_time = None
        self._last_update = None

    def start(self) -> None:
        self.reset()
        self._start_time = monotonic()
