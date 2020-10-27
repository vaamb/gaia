# TODO: rewrite all and integrate it in sensors
from queue import Queue
import os
from threading import Lock

from engine.config_parser import gaiaEngine_dir

_lock = Lock()


class basicCache:
    TYPE = "Basic"

    def __init__(self, cache_name, *args, **kwargs):
        self.name = cache_name


class dictCache(basicCache):
    TYPE = "Dict"

    def __init__(self, cache_name, *args, **kwargs):
        super().__init__(self, cache_name, *args, **kwargs)
        self._cache = {}

    def get(self):
        return self._cache

    def put(self, Dict):
        with _lock:
            self._cache.update(Dict)

    def clear(self):
        with _lock:
            self._cache = {}

    def remove(self, key):
        with _lock:
            del self._cache[key]

    def pop(self, key):
        with _lock:
            return self._cache.pop(key)


class persistantCache(basicCache):
    TYPE = "Persistant"

    def __init__(self, cache_name, *args, **kwargs):
        super().__init__(self, cache_name, *args, **kwargs)
        cache_folder = gaiaEngine_dir / "cache"
        if not os.path.exists(cache_folder):
            os.mkdir(cache_folder)
        self.file_path = cache_folder / f"{cache_name}.cch"
        print(self.file_path)

    def get(self):
        pass

    def put(self, _dict):
        with _lock:
            self._cache.update(_dict)

    def clear(self):
        with _lock:
            self._cache = {}

    def remove(self, key):
        with _lock:
            del self._cache[key]

    def pop(self, key):
        with _lock:
            self._cache.pop(key)


class queueCache(Queue):
    TYPE = "Queue"

    def __init__(self, cache_name, *args, **kwargs):
        self.name = cache_name
        super().__init__(self, *args, **kwargs)


class bidirectionnalQueue:
    TYPE = "BidirectionnalQ"

    def __init__(self):
        pass


CACHES_AVAILABLE = [basicCache,
                    persistantCache,
                    queueCache]


class cachesManager:
    def __init__(self):
        self.cache_dict = {}

    """
    def get_cache(self, cache_name, *args, **kwargs):
        if cache_name in self.cache_dict:
            cch = self.cache_dict[cache_name]
        else:
            cache_type = args[0]
            cch = self.create_cache(cache_name, cache_type)
            self.cache_dict[cache_name] = cch
        return cch
    """

    def create_cache(self, cache_name, cache_type, **kwargs):
        for cache in CACHES_AVAILABLE:
            if cache.TYPE == cache_type:
                cch = cache(cache_name, **kwargs)
                return cch
        raise ValueError("cache_name chould be in {[i.TYPE for i in CACHES_AVAILABLE]}")


manager = cachesManager()


def createCache(cache_name, cache_type):
    manager.create_cache(cache_name, cache_type)


"""
def getCache(cache_name, *args, **kwargs):
    manager.get_cache(cache_name, *args, **kwargs)
"""
