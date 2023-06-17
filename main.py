import eventlet

#eventlet.monkey_patch()

try:
    import orjson
    from orjson import dumps as _dumps
except ImportError:
    pass
else:
    from pydantic import BaseModel

    def _default(obj):
        if isinstance(obj, BaseModel):
            return obj.dict()
        raise TypeError

    def dumps(__obj, default=None, option=None):
        if default is None:
            return _dumps(__obj, default=_default, option=option)
        return _dumps(__obj, default=default, option=option)

    setattr(orjson, "dumps", dumps)

from gaia import main

main()
