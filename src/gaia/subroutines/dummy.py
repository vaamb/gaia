from gaia.subroutines.template import SubroutineTemplate


class Dummy(SubroutineTemplate):
    def _compute_if_manageable(self) -> bool:
        return True

    def _start(self) -> None:
        pass

    def _stop(self) -> None:
        pass

    def get_hardware_needed_uid(self) -> set[str]:
        return set()
