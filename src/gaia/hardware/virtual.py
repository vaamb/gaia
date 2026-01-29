from gaia.hardware.abc import Hardware


class virtualHardware(Hardware):
    __slots__ = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if (
            self.ecosystem is not None
            and self.ecosystem.engine.config.app_config.VIRTUALIZATION
        ):
            assert self.ecosystem.virtualized
