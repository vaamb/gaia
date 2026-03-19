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
            ecosystem_uid: str,
            **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        from gaia.virtual import VirtualEcosystem

        self.virtual_ecosystem = VirtualEcosystem.get(ecosystem_uid)


class virtualHardware:
    """Protocol mixin for virtual hardware. Expects `self.ecosystem: Ecosystem`."""
    __slots__ = ()

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
        VirtualEcosystem.get(self.ecosystem.uid)
