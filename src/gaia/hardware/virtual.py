from __future__ import annotations

import warnings

from gaia.hardware.abc import Hardware
from gaia.virtual import VirtualEcosystem


class VirtualDevice:
    def __init__(
            self,
            *args,
            ecosystem_uid: str,
            **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        from gaia.virtual import VirtualEcosystem

        self.virtual_ecosystem = VirtualEcosystem.get(ecosystem_uid)


class virtualHardware(Hardware):
    __slots__ = ()

    def __init__(self, *args, **kwargs):
        from gaia import GaiaConfigHelper

        if not GaiaConfigHelper.config_is_set():
            warnings.warn(
                "Using `WebSocketHardware.check_requirements()` will materialize "
                "Gaia's whole app configuration."
            )

        if not GaiaConfigHelper.get_config().VIRTUALIZATION:
            raise RuntimeError(
                "virtualHardware can only be used when virtualization is enabled"
            )

        super().__init__(*args, **kwargs)

        # Will raise if no corresponding VirtualEcosystem instance exists
        VirtualEcosystem.get(self.ecosystem.uid)
