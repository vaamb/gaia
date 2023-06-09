class EcosystemNotFound(ValueError):
    pass


class HardwareNotFound(ValueError):
    pass


class StoppingEcosystem(RuntimeError):
    pass


class StoppingSubroutine(RuntimeError):
    pass


class UndefinedParameter(ValueError):
    pass
