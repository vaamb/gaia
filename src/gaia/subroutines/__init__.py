from typing import Literal

from gaia.subroutines.climate import Climate
from gaia.subroutines.health import Health
from gaia.subroutines.light import Light
from gaia.subroutines.sensors import Sensors
from gaia.subroutines.template import SubroutineTemplate


SubroutineNames = Literal["sensors", "light", "climate", "health"]


subroutines: dict[SubroutineNames, type(SubroutineTemplate)] = {
    subroutine.__name__.lower(): subroutine for subroutine in [
        Sensors,
        Light,
        Climate,
        Health,
    ]
}
