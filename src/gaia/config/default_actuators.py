from typing import Literal, TypeAlias

import gaia_validators as gv

from gaia.config.migration import ClimateParameter, EnvironmentParameter, WeatherParameter


Direction = Literal["increase", "decrease"]
EnvironmentDirection: TypeAlias = tuple[EnvironmentParameter, Direction]
ClimateDirection: TypeAlias = tuple[ClimateParameter, Direction]
WeatherDirection: TypeAlias = tuple[WeatherParameter, Literal["increase"]]

# Default actuator groups name for the climate parameters
climate_to_group_mapping: dict[ClimateDirection, str] = {
    (gv.ClimateParameter.temperature.name, "increase"): str(gv.HardwareType.heater.name),
    (gv.ClimateParameter.temperature.name, "decrease"): str(gv.HardwareType.cooler.name),
    (gv.ClimateParameter.humidity.name, "increase"): str(gv.HardwareType.humidifier.name),
    (gv.ClimateParameter.humidity.name, "decrease"): str(gv.HardwareType.dehumidifier.name),
    (gv.ClimateParameter.light.name, "increase"): str(gv.HardwareType.light.name),
    (gv.ClimateParameter.wind.name, "increase"): str(gv.HardwareType.fan.name),
}


# Default actuator groups name for the weather parameters
weather_to_group_mapping: dict[WeatherDirection, str] = {
    (gv.WeatherParameter.rain.name, "increase"): "rainer",
    (gv.WeatherParameter.fog.name, "increase"): "fogger",
    (gv.WeatherParameter.wind_gust.name, "increase"): "fan",
}
