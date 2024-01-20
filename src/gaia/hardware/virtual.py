from gaia.hardware.abc import Hardware


class virtualHardware(Hardware):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if(
            self.subroutine is not None
            and self.subroutine.ecosystem.engine.config.app_config.VIRTUALIZATION
        ):
            assert self.subroutine.ecosystem.virtualized
