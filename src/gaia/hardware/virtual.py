from __future__ import annotations

import typing as t
import warnings

from gaia.hardware.abc import HardwareTypeHint
from gaia.virtual import VirtualEcosystem


class VirtualDevice:
    def __init__(
            self,
            *args,
            virtual_ecosystem: VirtualEcosystem,
            **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.virtual_ecosystem = virtual_ecosystem


class virtualHardwareMixin(HardwareTypeHint):
    """Protocol mixin for virtual hardware. Expects `self.ecosystem: Ecosystem`."""

    def __init__(self, *args, **kwargs):
        from gaia import GaiaConfigHelper

        if not GaiaConfigHelper.config_is_set():
            warnings.warn(
                "Instantiating `virtualHardwareMixin` without a config set will "
                "materialize Gaia's whole app configuration."
            )

        if not GaiaConfigHelper.get_config().VIRTUALIZATION:
            raise RuntimeError(
                "virtualHardware can only be used when virtualization is enabled"
            )

        super().__init__(*args, **kwargs)

        # Will raise if no corresponding VirtualEcosystem instance exists
        self.virtual_ecosystem = VirtualEcosystem.get(self.ecosystem_uid)
