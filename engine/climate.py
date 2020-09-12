#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Will be for heating, cooling, humidify, dehumidify, water plants"""

"""Add """

import random

class Chaos:
    def __init__(self, completeConfigObject):
        self.config = completeConfigObject
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

class gaiaClimate():
    def __init__(self, ecosystem):
        self.name = "climate"

    def __call__(self, sensors_data):
        pass