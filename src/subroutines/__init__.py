from .climate import Climate
from .health import Health
from .light import Light
from .sensors import Sensors
from .template import SubroutineTemplate


SUBROUTINES: dict[str, type(SubroutineTemplate)] = {
    subroutine.__name__.lower(): subroutine for subroutine in [
        Sensors,
        Light,
        # Health,
        Climate,
    ]
}
