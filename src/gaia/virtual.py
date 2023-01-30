from __future__ import annotations

from datetime import datetime, time, timedelta
from time import monotonic
import math

from gaia.utils import temperature_converter, SingletonMeta


SUNRISE = time(7, 0)
SUNSET = time(19, 0)


# 1 watt = 1 joule / sec
class VirtualWorld(metaclass=SingletonMeta):
    def __init__(
            self,
            equinox_sun_times: tuple = (SUNRISE, SUNSET),
            yearly_amp_sun_times: timedelta = timedelta(hours=2),
            # Actually half amp
            avg_temperature: float = 20.0,  # 12.5
            daily_amp_temperature: float = 4.0,  # 4.0
            yearly_amp_temperature: float = 0.0,  # 7.5
            avg_humidity: float = 50.0,  # 50.0
            amp_humidity: float = 10.0,  # 10.0
            avg_midday_light: int = 75000,
            yearly_amp_light: int = 25000,
    ) -> None:
        self._sunrise = equinox_sun_times[0]
        self._sunset = equinox_sun_times[1]
        self._yearly_amp_sun_times = yearly_amp_sun_times

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
        self._temperature = avg_temperature
        self._humidity = avg_humidity
        self._light = avg_midday_light
        self._dt = None
        self._last_update = None

    def __call__(self, time_now=None):
        mono_clock = monotonic()
        if (
            not self._last_update
            or mono_clock - self._last_update > 10
        ):
            self._last_update = mono_clock
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
                day_factor = - math.sin((seconds_since_sunset / nighttime) * math.pi)
                light = 0
                self._light = int(light)

            temperature = (
                base_temperature +
                self._params["temperature"]["yearly_amp"] * season_factor +
                self._params["temperature"]["daily_amp"] * day_factor
            )
            self._temperature = round(temperature, 2)
            humidity = (
                self._params["humidity"]["avg"] -
                self._params["humidity"]["amp"] * day_factor
            )
            self._humidity = round(humidity, 2)

        return self.temperature, self.humidity, self.light

    @property
    def temperature(self):
        return self._temperature

    @property
    def humidity(self):
        return self._humidity

    @property
    def light(self):
        return self._light


class VirtualEcosystem:
    AIR_HEAT_CAPACITY = 1  # kj/kg/K
    AIR_DENSITY = 1.225  # kg/m3
    WATER_HEAT_CAPACITY = 4.184  # kj/kg/K
    INSULATION_U_VAL = 3.5  # W/m2K -> Assumes a ~ 5mm polyacrylate sheet

    def __init__(
            self,
            uid: str,
            virtual_world: VirtualWorld,
            dimension: tuple = (0.5, 0.5, 1.0),
            water_volume: float = 5,  # in liter
            max_heater_output: int = 25,  # max heater output in watt
            max_light_output: int = 30000,  # max light output in lux
            start: bool = False,
    ) -> None:

        assert len(dimension) == 3
        self.virtual_world = virtual_world
        self._uid = uid
        self._volume = dimension[0] * dimension[1] * dimension[2]
        # Assumes only loss through walls
        self._exchange_surface = (
                2 * dimension[2] * (dimension[0] + dimension[1])
        )
        self._heat_loss_coef = self._exchange_surface * self.INSULATION_U_VAL  # in W/K
        self._water_volume = water_volume

        self._temperature = None
        self._humidity = None
        self._lux = None

        self._max_heater_output = max_heater_output
        self._max_light_output = max_light_output

        self._heat_quantity = None
        self._hybrid_capacity = None
        self._air_water_capacity_ratio = None, None

        # Virtual hardware status
        self._heater = False
        self._light = False

        self._start_time = None
        self._last_update = None

        if start:
            self.start()

    def measure(self):
        delay_between_measures = 15

        if not self._start_time:
            raise RuntimeError("The virtualEcosystem needs to be started "
                               "before computing measures")
        now = monotonic()
        if (
                not self._last_update
                or (now - self._last_update) > delay_between_measures
        ):
            if self._last_update is None:
                d_sec = 0.1
            else:
                d_sec = (monotonic() - self._last_update)
            out_temp, out_hum, out_light = self.virtual_world()

            # New heat quantity
            d_temp = self.temperature - out_temp
            heat_quantity = self._heat_quantity
            heat_quantity -= self._heat_loss_coef * d_sec * d_temp
            if self._heater:
                heat_quantity += (self._max_heater_output * d_sec)
            self._heat_quantity = heat_quantity

            # Temperature calculation
            self._temperature = self._heat_quantity / self._hybrid_capacity

            # Humidity calculation
            self._humidity = out_hum

            # Light calculation
            self._lux = out_light
            if self._light:
                self._lux += self._max_light_output
            self._last_update = now

    def reset(self):
        air_mass = self._volume * self.AIR_DENSITY
        air_capacity = air_mass * self.AIR_HEAT_CAPACITY * 1000

        water_mass = self._water_volume
        water_capacity = water_mass * self.WATER_HEAT_CAPACITY * 1000

        self._hybrid_capacity = air_capacity + water_capacity

        self._air_water_capacity_ratio = (
            round(air_capacity / self._hybrid_capacity, 2),
            round(water_capacity / self._hybrid_capacity, 2)
        )

        out_temp, out_hum, out_light = self.virtual_world()
        k_temperature = temperature_converter(out_temp, "c", "k")

        self._heat_quantity = self._hybrid_capacity * k_temperature

        self._temperature = k_temperature
        self._humidity = out_hum
        self._lux = out_light

        self._start_time = None
        self._last_update = None

    def start(self):
        self.reset()
        self._start_time = datetime.now()

    @property
    def uid(self):
        return self._uid

    @uid.setter
    def uid(self, value):
        raise AttributeError("uid cannot be set")

    @property
    def temperature(self) -> float | None:
        return temperature_converter(self._temperature, "k", "c")

    @property
    def humidity(self) -> float | None:
        return self._humidity

    @property
    def lux(self):
        return self._lux

    @property
    def uptime(self):
        return datetime.now() - self._start_time

    @property
    def status(self):
        return bool(self._start_time)


_virtual_ecosystems: dict[str, VirtualEcosystem] = {}


def get_virtual_ecosystem(ecosystem: str, start=False) -> VirtualEcosystem:
    from gaia.config import get_environment_IDs
    ecosystem_uid = get_environment_IDs(ecosystem).uid
    try:
        return _virtual_ecosystems[ecosystem_uid]
    except KeyError:
        if start:
            _virtual_ecosystems[ecosystem_uid] = \
                VirtualEcosystem(ecosystem_uid, VirtualWorld(), start=True)
        else:
            _virtual_ecosystems[ecosystem_uid] = \
                VirtualEcosystem(ecosystem_uid, VirtualWorld())
        return _virtual_ecosystems[ecosystem_uid]
