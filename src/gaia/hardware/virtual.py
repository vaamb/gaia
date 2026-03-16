from __future__ import annotations

import typing
import warnings

from gaia.hardware.abc import Hardware
from gaia.virtual import VirtualEcosystem


if typing.TYPE_CHECKING:
    from gaia.ecosystem import Ecosystem


class VirtualDevice:
    def __init__(
            self,
            *args,
            ecosystem: Ecosystem,
            **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.virtual_ecosystem = ecosystem.virtual_self


class virtualHardware(Hardware):
    __slots__ = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        from gaia import GaiaConfigHelper

        if not GaiaConfigHelper.config_is_set():
            warnings.warn(
                "Using `WebSocketHardware.check_requirements()` will materialize "
                "Gaia's whole app configuration."
            )
        if GaiaConfigHelper.get_config().VIRTUALIZATION:
            # Will raise if no corresponding VirtualEcosystem instance exists
            VirtualEcosystem.get(self.ecosystem.uid)
