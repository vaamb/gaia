from typing import Literal, NamedTuple, TypeAlias

import gaia_validators as gv


Direction = Literal["increase", "decrease"]
EnvironmentDirection: TypeAlias = tuple[gv.EnvironmentParameter, Direction]
ClimateDirection: TypeAlias = tuple[gv.ClimateParameter, Direction]
WeatherDirection: TypeAlias = tuple[gv.WeatherParameter, Literal["increase"]]


class PIDParameters(NamedTuple):
    Kp: float
    Ki: float
    Kd: float


# Default actuator groups name for the climate parameters
climate_to_group_mapping: dict[ClimateDirection, str] = {
    ("temperature", "increase"): str(gv.HardwareType.heater.name),
    ("temperature", "decrease"): str(gv.HardwareType.cooler.name),
    ("humidity", "increase"): str(gv.HardwareType.humidifier.name),
    ("humidity", "decrease"): str(gv.HardwareType.dehumidifier.name),
    ("light", "increase"): str(gv.HardwareType.light.name),
    ("wind", "increase"): str(gv.HardwareType.fan.name),
}


DEFAULT_CLIMATE_PARAMETERS = frozenset(p for p, _ in climate_to_group_mapping)


# Default actuator groups name for the weather parameters
weather_to_group_mapping: dict[WeatherDirection, str] = {
    ("rain", "increase"): "rainer",
    ("fog", "increase"): "fogger",
    ("wind_gust", "increase"): "fan",
}


DEFAULT_WEATHERS_PARAMETERS = frozenset(p for p, _ in weather_to_group_mapping)


# Default PID values for the default climate parameters
pid_values: dict[gv.ClimateParameter, PIDParameters] = {
    "temperature": PIDParameters(5.0, 0.5, 0.1),
    "humidity": PIDParameters(2.0, 0.5, 1.0),
    "light": PIDParameters(0.001, 0.0, 0.0),
    "wind": PIDParameters(1.0, 0.0, 0.0),
}
