class Error(Exception):
    pass

class InvalidEcosystem(ValueError):
    """The Ecosystem given cannot be found in the configuration file"""
    pass