from typing import Literal, TypedDict

from gaia.subroutines.climate import Climate
from gaia.subroutines.health import Health
from gaia.subroutines.light import Light
from gaia.subroutines.sensors import Sensors
from gaia.subroutines.pictures import Pictures
from gaia.subroutines.template import SubroutineTemplate


SubroutineNames = Literal["sensors", "light", "climate", "pictures", "health"]

# Sensors and light subroutines need to remain first as other subroutines depend
#  on them
subroutine_names: list[SubroutineNames] = [
    "sensors",
    "light",
    "climate",
    "pictures",
    "health",
]


class SubroutineDict(TypedDict):
    sensors: Sensors
    light: Light
    climate: Climate
    pictures: Pictures
    health: Health


subroutine_dict: SubroutineDict = {
    subroutine.__name__.lower(): subroutine
    for subroutine in [
        Sensors,
        Light,
        Climate,
        Pictures,
        Health,
    ]
}
