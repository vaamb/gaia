from gaia.hardware.abc import Hardware
from gaia.virtual import get_virtual_ecosystem


class virtualHardware(Hardware):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if(
            self.subroutine is not None
            and self.subroutine.ecosystem.engine.config.app_config.VIRTUALIZATION
        ):
            # Check if the virtual ecosystem exists
            get_virtual_ecosystem(self.subroutine.ecosystem.uid)
