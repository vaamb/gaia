__version__ = "0.6.2"

import typing as t


if t.TYPE_CHECKING:
    from gaia.config import (
        EcosystemConfig, EngineConfig, get_base_dir, get_config)
    from gaia.ecosystem import Ecosystem
    from gaia.engine import Engine
    from gaia.main import main
    from gaia.shared_resources import get_scheduler, start_scheduler
else:
    from importlib import import_module

    from gaia.main import main

    def __getattr__(name):
        if name in (
                "EcosystemConfig", "EngineConfig", "get_base_dir", "get_config"):
            return getattr(import_module("gaia.config"), name)
        if name == "Ecosystem":
            return getattr(import_module("gaia.ecosystem"), name)
        if name == "Engine":
            return getattr(import_module("gaia.engine"), name)
        if name in ("get_scheduler", "start_scheduler"):
            return getattr(import_module("gaia.shared_resources"), name)
