from gaia.hardware.abc import Hardware
from gaia.virtual import get_virtual_ecosystem


class virtualHardware(Hardware):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from gaia import get_config
        if get_config().VIRTUALIZATION:
            get_virtual_ecosystem(self.subroutine.ecosystem.uid, start=True)
