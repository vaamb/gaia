__version__ = "0.8.0"

import typing as t


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.config import (
        BaseConfig, EcosystemConfig, EngineConfig, GaiaConfigHelper)
    from gaia.ecosystem import Ecosystem
    from gaia.engine import Engine
    from gaia.main import main
else:
    from importlib import import_module

    from gaia.main import main

    def __getattr__(name):
        if name in (
                "BaseConfig", "EcosystemConfig", "EngineConfig", "GaiaConfigHelper"):
            return getattr(import_module("gaia.config"), name)
        if name == "Ecosystem":
            return getattr(import_module("gaia.ecosystem"), name)
        if name == "Engine":
            return getattr(import_module("gaia.engine"), name)
