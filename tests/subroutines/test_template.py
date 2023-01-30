import typing as t


if t.TYPE_CHECKING:
    from gaia.subroutines import Climate, Light, Sensors


def test_not_manageable(
        subroutines_list: list["Climate", "Light", "Sensors"]
):
    for subroutine in subroutines_list:
        subroutine.management = True
        subroutine.update_manageable()
        assert subroutine.manageable is False


def test_properties(
        subroutines_list: list["Climate", "Light", "Sensors"],
        ecosystem
):
    for subroutine in subroutines_list:
        assert subroutine.ecosystem.__dict__ is ecosystem.__dict__
        assert subroutine.config.__dict__ is ecosystem.config.__dict__
        assert subroutine.status is False
