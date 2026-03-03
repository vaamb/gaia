from typing import Literal, TypeAlias

import gaia_validators as gv


EnvironmentParameter: TypeAlias = gv.ClimateParameter | gv.WeatherParameter


# Default actuator couples for the climate parameters
climate_actuator_couples: dict[gv.ClimateParameter, gv.ActuatorCouple] = {
    gv.ClimateParameter.temperature: gv.ActuatorCouple(
        increase=str(gv.HardwareType.heater.name),
        decrease=str(gv.HardwareType.cooler.name)),
    gv.ClimateParameter.humidity: gv.ActuatorCouple(
        increase=str(gv.HardwareType.humidifier.name),
        decrease=str(gv.HardwareType.dehumidifier.name)),
    gv.ClimateParameter.light: gv.ActuatorCouple(
        increase=str(gv.HardwareType.light.name),
        decrease=None),
    gv.ClimateParameter.wind: gv.ActuatorCouple(
        increase=str(gv.HardwareType.fan.name),
        decrease=None),
}


assert all([
    climate_parameter in climate_actuator_couples
    for climate_parameter in gv.ClimateParameter
])


def get_actuator_to_parameter(
        actuator_couples: dict[EnvironmentParameter, gv.ActuatorCouple],
) -> dict[str, EnvironmentParameter]:
    return {
        actuator: climate_parameter
        for climate_parameter, actuator_couple in actuator_couples.items()
        for actuator in actuator_couple
        if actuator is not None
    }


actuator_to_parameter = get_actuator_to_parameter(climate_actuator_couples)  # ty: ignore[invalid-argument-type]


def get_actuator_to_direction(
        actuator_couples: dict[EnvironmentParameter, gv.ActuatorCouple],
) -> dict[str, Literal["increase", "decrease"]]:
    return {
        actuator: direction
        for climate_parameter, actuator_couple in actuator_couples.items()
        for actuator, direction in zip((actuator_couple), ("increase", "decrease"))
        if actuator is not None
    }
