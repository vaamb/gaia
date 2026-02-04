class NotFound(ValueError):
    pass


class SubroutineNotFound(NotFound):
    pass


class EcosystemNotFound(NotFound):
    pass


class HardwareNotFound(NotFound):
    pass


class PlantNotFound(NotFound):
    pass


class NoSubroutineNeeded(RuntimeError):
    pass


class StoppingSubroutine(RuntimeError):
    pass


class UndefinedParameter(ValueError):
    pass
