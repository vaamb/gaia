from typing import Literal, TypeAlias

import gaia_validators as gv


Direction = Literal["increase", "decrease"]
EnvironmentParameter: TypeAlias = gv.ClimateParameter | gv.WeatherParameter
EnvironmentDirection: TypeAlias = tuple[gv.ClimateParameter | gv.WeatherParameter, Direction]


# Default actuator groups name for the climate parameters
climate_to_group_mapping: dict[tuple[gv.ClimateParameter, Direction], str] = {
    (gv.ClimateParameter.temperature, "increase"): str(gv.HardwareType.heater.name),
    (gv.ClimateParameter.temperature, "decrease"): str(gv.HardwareType.cooler.name),
    (gv.ClimateParameter.humidity, "increase"): str(gv.HardwareType.humidifier.name),
    (gv.ClimateParameter.humidity, "decrease"): str(gv.HardwareType.dehumidifier.name),
    (gv.ClimateParameter.light, "increase"): str(gv.HardwareType.light.name),
    (gv.ClimateParameter.wind, "increase"): str(gv.HardwareType.fan.name),
}


# Default actuator groups name for the weather parameters
weather_to_group_mapping: dict[tuple[gv.WeatherParameter, Direction], str] = {
    (gv.WeatherParameter.rain, "increase"): "rainer",
    (gv.WeatherParameter.fog, "increase"): "fogger",
    (gv.WeatherParameter.wind_gust, "increase"): "fan",
}


environment_to_group_mapping: dict[tuple[EnvironmentParameter, Direction], str] = {
    **climate_to_group_mapping,
    **weather_to_group_mapping,
}


actuator_to_parameter: dict[str, EnvironmentParameter] = {
    actuator_group: environment_direction[0]
    for environment_direction, actuator_group in environment_to_group_mapping.items()
}


actuator_to_direction: dict[str, Direction] = {
    actuator_group: environment_direction[1]
    for environment_direction, actuator_group in environment_to_group_mapping.items()
}
