from typing import Literal, TypeAlias

import gaia_validators as gv

from gaia.config.migration import ClimateParameter, EnvironmentParameter, WeatherParameter


Direction = Literal["increase", "decrease"]
EnvironmentDirection: TypeAlias = tuple[EnvironmentParameter, Direction]
ClimateDirection: TypeAlias = tuple[ClimateParameter, Direction]
WeatherDirection: TypeAlias = tuple[WeatherParameter, Literal["increase"]]

# Default actuator groups name for the climate parameters
climate_to_group_mapping: dict[ClimateDirection, str] = {
    ("temperature", "increase"): str(gv.HardwareType.heater.name),
    ("temperature", "decrease"): str(gv.HardwareType.cooler.name),
    ("humidity", "increase"): str(gv.HardwareType.humidifier.name),
    ("humidity", "decrease"): str(gv.HardwareType.dehumidifier.name),
    ("light", "increase"): str(gv.HardwareType.light.name),
    ("wind", "increase"): str(gv.HardwareType.fan.name),
}


# Default actuator groups name for the weather parameters
weather_to_group_mapping: dict[WeatherDirection, str] = {
    ("rain", "increase"): "rainer",
    ("fog", "increase"): "fogger",
    ("wind_gust", "increase"): "fan",
}
