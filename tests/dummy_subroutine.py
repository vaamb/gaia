from gaia.subroutines.template import SubroutineTemplate


class Dummy(SubroutineTemplate):
    manageable_state = True

    def _compute_if_manageable(self) -> bool:
        return self.manageable_state

    def _start(self) -> None:
        pass

    def _stop(self) -> None:
        pass

    def get_hardware_needed_uid(self) -> set[str]:
        return set()
