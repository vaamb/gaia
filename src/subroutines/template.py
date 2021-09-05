import logging
import weakref


class SubroutineTemplate:
    NAME = "subroutineTemplate"

    def __init__(self, engine) -> None:
        self._engine = weakref.proxy(engine)
        self._config = self._engine._config
        self._uid = self._config.uid
        self._ecosystem = self._config.name
        self._subroutine_name = f"gaia{self.NAME.capitalize()}"
        self._logger = logging.getLogger(
            f"eng.{self._ecosystem}.{self.NAME.capitalize()}"
        )
        self._logger.debug(f"Initializing {self._subroutine_name}")
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
            self._logger.debug(f"Starting {self._subroutine_name}")
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
            self._logger.debug(f"Stopping {self._subroutine_name}")
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
        self._engine = weakref.proxy(engine)

    def del_engine(self) -> None:
        self._engine = None
