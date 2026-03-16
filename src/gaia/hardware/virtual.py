from __future__ import annotations

import typing

from gaia.hardware.abc import Hardware


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
        if self.ecosystem.engine.config.app_config.VIRTUALIZATION:
            assert self.ecosystem.virtualized
