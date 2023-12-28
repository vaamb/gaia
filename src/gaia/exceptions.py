class NonValidSubroutine(ValueError):
    pass


class EcosystemNotFound(ValueError):
    pass


class HardwareNotFound(ValueError):
    pass


class NoSubroutineNeeded(RuntimeError):
    pass


class StoppingSubroutine(RuntimeError):
    pass


class UndefinedParameter(ValueError):
    pass
