from enum import IntFlag

from pydantic import Field, RootModel

import gaia_validators as gv

from gaia.config import from_files
from gaia.subroutines.template import SubroutineTemplate


# Patched gaia_validators.ManagementFlags to add the dummy subroutine
management_flags = {
    flag.name: flag.value
    for flag in gv.ManagementFlags.__members__.values()
}
max_flag = max(*[flag.value for flag in gv.ManagementFlags])
management_flags["dummy"] = management_flags["dummy_enabled"] = max_flag * 2
PatchedManagementFlags = IntFlag("ManagementFlags", management_flags)


# Patched gaia_validators.ManagementConfig to add the dummy subroutine
class PatchedManagementConfig(gv.ManagementConfig):
    dummy: bool = False


class EcosystemConfigValidator(from_files.EcosystemConfigValidator):
    management: gv.ManagementConfig = Field(default_factory=gv.ManagementConfig)


class PatchedRootEcosystemsConfigValidator(RootModel):
    root: dict[str, EcosystemConfigValidator]


class Dummy(SubroutineTemplate):
    _hardware_choices = {}

    manageable_state = True

    def _compute_if_manageable(self) -> bool:
        return self.manageable_state

    async def _start(self) -> None:
        pass

    async def _stop(self) -> None:
        pass

    def get_hardware_needed_uid(self) -> set[str]:
        return set()

    async def _routine(self) -> None:
        pass
