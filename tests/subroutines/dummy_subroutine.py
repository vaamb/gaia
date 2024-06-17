from gaia.subroutines.template import SubroutineTemplate


class Dummy(SubroutineTemplate):
    manageable_state = True

    def _compute_if_manageable(self) -> bool:
        return self.manageable_state

    async def _start(self) -> None:
        pass

    async def _stop(self) -> None:
        pass

    def get_hardware_needed_uid(self) -> set[str]:
        return set()

    async def routine(self) -> None:
        pass
