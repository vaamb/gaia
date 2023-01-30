from gaia.subroutines.climate import Climate
from gaia.subroutines.health import Health
from gaia.subroutines.light import Light
from gaia.subroutines.sensors import Sensors
from gaia.subroutines.template import SubroutineTemplate


SUBROUTINES: dict[str, type(SubroutineTemplate)] = {
    subroutine.__name__.lower(): subroutine for subroutine in [
        Sensors,
        Light,
        # Health,
        Climate,
    ]
}
