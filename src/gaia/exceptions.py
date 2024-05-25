class NonValidSubroutine(ValueError):
    pass


class NotFound(ValueError):
    pass


class EcosystemNotFound(NotFound):
    pass


class HardwareNotFound(NotFound):
    pass


class NoSubroutineNeeded(RuntimeError):
    pass


class StoppingSubroutine(RuntimeError):
    pass


class UndefinedParameter(ValueError):
    pass
