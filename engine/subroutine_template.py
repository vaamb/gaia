import logging
import weakref

from engine.config_parser import configWatchdog, getConfig


class subroutineTemplate:
    NAME = "subroutineTemplate"

    def __init__(self, ecosystem: str = None, engine=None) -> None:
        assert ecosystem or engine
        if engine:
            # Use weakref for the circular ref
            self._engine = weakref.proxy(engine)
            if ecosystem:
                assert ecosystem in (engine.name, engine.uid)
            self._config = self._engine._config
        else:
            self._engine = None
            self._config = getConfig(ecosystem)
        self._ecosystem = self._config.name
        self._subroutine_name = f"gaia{self.NAME.capitalize()}"
        self._logger = logging.getLogger(f"eng.{self._ecosystem}."
                                         f"{self.NAME.capitalize()}")
        self._logger.info(f"Initializing {self._subroutine_name}")
        self._started = False

    def _finish__init__(self):
        self._logger.debug(f"{self._subroutine_name} successfully "
                           f"initialized")

    def _start(self):
        print(f"_start method was not overwritten for {self.NAME}")

    def _stop(self):
        print(f"_stop method was not overwritten for {self.NAME}")

    def start(self):
        if not self._started:
            if not self._engine:
                configWatchdog.start()
            self._logger.info(f"Starting {self._subroutine_name}")
            try:
                self._start()
                self._started = True
                self._logger.debug(f"{self._subroutine_name} "
                                   f"successfully started")
            except Exception as e:
                self._logger.error(
                    f"{self._subroutine_name} was not "
                    f"successfully started. ERROR msg: {e}")
                raise e
        else:
            raise RuntimeError(f"{self._subroutine_name} is "
                               f"already running")

    def stop(self):
        if self._started:
            if not self._engine:
                configWatchdog.stop()
            self._logger.info(f"Stopping {self._subroutine_name}")
            try:
                self._stop()
                self._started = False
                self._logger.debug(f"{self._subroutine_name} "
                                   f"successfully stopped")
            except Exception as e:
                self._logger.error(
                    f"{self._subroutine_name} was not "
                    f"successfully stopped. ERROR msg: {e}")
                raise e

    @property
    def status(self) -> bool:
        return self._started

    def add_engine(self, engine) -> None:
        ref = weakref.ref(engine)
        self._engine = ref()

    def del_engine(self) -> None:
        self._engine = None
