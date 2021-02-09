import random

from engine.subroutine_template import subroutineTemplate


class Chaos:
    def __init__(self, ecosystem):
        configWatchdog.start()
        self.config = getConfig(ecosystem)
        self.ecosystem = self.config.name
        
        self.chaos_factor = 10 #self.config.chaos_factor
        if self.chaos_factor != 0:
            self.max_duration = 10
            pass

        self.chaos = 0        
        self.duration = random.randint(1, self.max_duration) 

    def __call__(self):
        if self.chaos_factor != 0:

            if self.chaos == 0:
                rdm = random.randint(1, self.chaos_factor)
                if rdm == 1:
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

        self._finish__init__()
