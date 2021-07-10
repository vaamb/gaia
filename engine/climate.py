import random

from simple_pid import PID

from engine.subroutine_template import subroutineTemplate


Kp = 0.01
Ki = 0.005
Kd = 0.01


class Chaos:
    def __init__(self, chaos_factor=10):
        self.chaos_factor = chaos_factor
        if self.chaos_factor != 0:
            self.max_duration = 10
            pass

        self.chaos = 0        
        self.duration = random.randint(1, self.max_duration) 

    def __call__(self):
        if self.chaos_factor != 0:

            if self.chaos == 0:
                _random = random.randint(1, self.chaos_factor)
                if _random == 1:
                    self.chaos = 1

            elif self.chaos == self.duration:
                self.duration = random.randint(1, self.max_duration) 
                self.chaos = 0

            else:
                self.chaos += 1

            return self.chaos


class gaiaClimate(subroutineTemplate):
    NAME = "climate"

    def __init__(self, ecosystem=None, engine=None) -> None:
        super().__init__(ecosystem=ecosystem, engine=engine)

        self._chaos = Chaos()
        self._regulators = {
            "heaters": {"list": [],
                        "PID": None},
            "coolers": {"list": [],
                        "PID": None},
            "humidifiers": {"list": [],
                            "PID": None},
            "dehumidifiers": {"list": [],
                              "PID": None},
            "fans": {"list": []}
        }

        self._parameters = {
            "temperature": self._config.get_climate_parameters("temperature"),
            "humidity": self._config.get_climate_parameters("humidity"),

        }

        self._finish__init__()

    # TODO: get heaters and coolers from config
    # TODO: if day and night parameters: use them, else, use 8h - 20h
