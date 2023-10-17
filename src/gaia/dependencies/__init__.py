from __future__ import annotations

from typing import Literal


Module = Literal["camera", "database", "dispatcher"]


def check_dependencies(module: Module | list[Module]) -> None:
    if isinstance(module, str):
        module = [module]
    if "camera" in module:
        from .camera import check_dependencies
        check_dependencies()
    if "database" in module:
        from .database import check_dependencies
        check_dependencies()
    if "dispatcher" in module:
        from .dispatcher import check_dependencies
        check_dependencies()
