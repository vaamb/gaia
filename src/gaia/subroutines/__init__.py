from typing import Literal, TypedDict

from gaia.subroutines.climate import Climate
from gaia.subroutines.health import Health
from gaia.subroutines.light import Light
from gaia.subroutines.sensors import Sensors
from gaia.subroutines.pictures import Pictures
from gaia.subroutines.template import SubroutineTemplate
from gaia.subroutines.weather import Weather


SubroutineNames = Literal["sensors", "light", "climate", "weather", "pictures", "health"]

# Sensors and light subroutines need to remain first as other subroutines depend
#  on them
subroutine_names: list[SubroutineNames] = [
    "sensors",
    "light",
    "climate",
    "weather",
    "pictures",
    "health",
]


class SubroutineDict(TypedDict):
    sensors: type[Sensors]
    light: type[Light]
    climate: type[Climate]
    weather: type[Weather]
    pictures: type[Pictures]
    health: type[Health]


subroutine_dict: SubroutineDict = {
    "sensors": Sensors,
    "light": Light,
    "climate": Climate,
    "weather": Weather,
    "pictures": Pictures,
    "health": Health,
}
