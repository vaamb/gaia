from __future__ import annotations

import typing as t
import warnings

from gaia.virtual import VirtualEcosystem


if t.TYPE_CHECKING:
    from gaia import Ecosystem


class VirtualDevice:
    def __init__(
            self,
            *args,
            virtual_ecosystem: VirtualEcosystem,
            **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.virtual_ecosystem = virtual_ecosystem


class virtualHardware:
    """Protocol mixin for virtual hardware. Expects `self.ecosystem: Ecosystem`."""

    if t.TYPE_CHECKING:
        ecosystem: Ecosystem

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
        self.virtual_ecosystem = VirtualEcosystem.get(self.ecosystem.uid)
