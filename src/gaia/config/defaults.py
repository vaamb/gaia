from typing import Literal

import gaia_validators as gv


actuator_couples: dict[gv.ClimateParameter, gv.ActuatorCouple] = {
    gv.ClimateParameter.temperature: gv.ActuatorCouple(
        increase=gv.HardwareType.heater, decrease=gv.HardwareType.cooler),
    gv.ClimateParameter.humidity: gv.ActuatorCouple(
        increase=gv.HardwareType.humidifier, decrease=gv.HardwareType.dehumidifier),
    gv.ClimateParameter.light: gv.ActuatorCouple(
        increase=gv.HardwareType.light, decrease=None),
    gv.ClimateParameter.wind: gv.ActuatorCouple(
        increase=gv.HardwareType.fan, decrease=None),
}


assert all([
    climate_parameter in actuator_couples
    for climate_parameter in gv.ClimateParameter
])


def get_actuator_to_parameter(
        actuator_couples: dict[gv.ClimateParameter, gv.ActuatorCouple],
) -> dict[str, gv.ClimateParameter]:
    return {
        actuator: climate_parameter
        for climate_parameter, actuator_couple in actuator_couples.items()
        for actuator in actuator_couple
        if actuator is not None
    }

actuator_to_parameter = get_actuator_to_parameter(actuator_couples)


def get_actuator_to_direction(
        actuator_couples: dict[gv.ClimateParameter, gv.ActuatorCouple],
) -> dict[str, Literal["increase", "decrease"]]:
    return {
        actuator: direction
        for climate_parameter, actuator_couple in actuator_couples.items()
        for actuator, direction in zip((actuator_couple), ("increase", "decrease"))
        if actuator is not None
    }

actuator_to_direction = get_actuator_to_direction(actuator_couples)
