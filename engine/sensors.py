#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Do i2c config

Ex with VEML7700:
i2c = busio.I2C(board.SCL, board.SDA)
VEML7700 = adafruit_veml7700.VEML7700(i2c)

data = VEML7700.lux
"""

import logging
import time
from time import sleep
from datetime import datetime
from threading import Thread

try:
    import RPi.GPIO as GPIO
except:
    from stupid_PI import GPIO

try:
    import Adafruit_DHT as dht
except:
    from stupid_PI import dht

#import board
#import busio
#import adafruit_veml7700

from .utils import pin_translation


#only for dvlpmt:
import random

SENSORS_TIMEOUT = 30


class gaiaSensors():
    def __init__(self, completeConfigObject):
        self._config = completeConfigObject
        self._ecosystem = self._config.name
        self.name = "sensors"
        self._logger = logging.getLogger(f"eng.{self._ecosystem}.Sensors")
        self._logger.debug(f"Initializing gaiaSensors for {self._ecosystem}")
        self._timezone = self._config.local_timezone

        self._start_sensors_loop()
        self._logger.debug(f"gaiaSensors has been initialized for {self._ecosystem}")

    def _setup_hardware(self):
#        GPIO.setmode(GPIO.BOARD)
#        for sensor in self._config.get_hardware_group("sensor", "environment"):

            #General sensor setup
#            if sensor not in ["DHT22", "VEML7700"]:
#                pin = self._config.get_hardware_pin(sensor)
#               GPIO.setup(self.pin_light, GPIO.IN)
#                if sensor in ["myLowSensors"]:
#                    GPIO.output(pin, GPIO.LOW)
#                elif sensor in ["myHighSensors"]:
#                    GPIO.output(pin, GPIO.HIGH)
        pass

    def _create_sensors_dict(self):
        #Create the dictionnary that will hold the values read by the sensors
        #Create the base of the dictionnary
        self._sensors_dict = {"datetime": ""}

        #Add environmental sensors if there are any
        environmental_measures = self._config.get_measure_list("environment")
        if environmental_measures != []:
            environmental_measures = set(environmental_measures)
            self._sensors_dict["environment"] = {}
            for measure in environmental_measures:
                self._sensors_dict["environment"][measure] = {}

        #Add plants with sensors if there are any
        plant_sensors = self._config.get_hardware_group("sensor", "plant")
        if plant_sensors != []:
            self._sensors_dict["plants"] = {}
            for sensor in plant_sensors:
                measure = self._config.hardware_dict[sensor]["measure"]
                plant = self._config.hardware_dict[sensor]["plant"]
                self._sensors_dict["plants"][plant] = {measure: {}}

    def _start_sensors_loop(self):
        self._logger.debug(f"Starting sensors loop for {self._ecosystem}")
        self.refresh_hardware()
        self._run = True
        self._thread = Thread(target=self._sensors_loop, args=())
        self._thread.start()
        self._logger.debug(f"Sensors loop started for {self._ecosystem}")

    def _stop_sensors_loop(self):
        self._logger.debug(f"Stopping sensors loop for {self._ecosystem}")
        try:
            self._run = False
            del self._thread
            self._logger.debug(f"Sensors loop was stopped for {self._ecosystem}")
        except:
            self._logger.error(f"Sensors loop was not stopped properly for {self._ecosystem}")

    def _sensors_loop(self):
        while self._run:
            start_time = time.time()
            self._update_sensors_data()
            loop_time = time.time() - start_time
            if loop_time < 0.1:
                loop_time = 0.1
            sleep(SENSORS_TIMEOUT - loop_time)

    def _update_sensors_data(self):
        """
        Loops through all the sensors and stores the value in self._sensors_dict
        """
        self._logger.debug(f"gaiaSensors starting the sensors routine for {self._ecosystem}")

        #Set time
        now = datetime.now().replace(microsecond = 0)
        now_tz = now.astimezone(self._timezone)
        self._sensors_dict["datetime"] = now_tz

        #Loop through environmental sensors
        for sensor in self._config.get_hardware_group("sensor", "environment"):
            pin = self._config.hardware_dict[sensor]["pin"]
            sensor_model = self._config.hardware_dict[sensor]["model"]

            #Support for DHT22
            if sensor_model == "DHT22":
                pin_BCM = pin_translation(pin, "to_BCM")
                humidity, temperature = dht.read_retry(dht.DHT22, pin_BCM)
                humidity = round(humidity, 1)
                temperature = round(temperature, 1)
                self._sensors_dict["environment"]["humidity"][sensor] = humidity
                self._sensors_dict["environment"]["temperature"][sensor] = temperature

            #Support for VEML7700
            if sensor_model == "VEML7700":

                #For dvlpment only
                light = random.randrange(1000, 25000, 10)

                self._sensors_dict["environment"]["light"][sensor] = light

            #For development purpose only
            if sensor_model == "myMegaSensor":
                temperature_data = random.uniform(17, 30)
                humidity_data = random.uniform(20, 55)
                light_data = random.randrange(1000, 100000, 10)
                self._sensors_dict["environment"]["temperature"][sensor] = round(temperature_data, 1)
                self._sensors_dict["environment"]["humidity"][sensor] = round(humidity_data, 1)
                self._sensors_dict["environment"]["light"][sensor] = light_data

            sleep(0.1)

        #Loop through plant sensors
        for sensor in self._config.get_hardware_group("sensor", "plant"):
            pin = self._config.hardware_dict[sensor]["pin"]
            measure = self._config.hardware_dict[sensor]["measure"]
            plant = self._config.hardware_dict[sensor]["plant"]

            if self._config.hardware_dict[sensor]["model"] == "moisture1":

                #For development only
                sensor_data = round(random.uniform(10, 40), 2)

                self._sensors_dict["plants"][plant][measure][sensor] = sensor_data

            sleep(0.1)

        self._logger.debug("gaiaSensors finished the sensors routine for {}".format(self._ecosystem))

    """API calls"""
    #Configuration info
    def refresh_hardware(self):
        self._setup_hardware()
        self._create_sensors_dict()

    #sensor
    @property
    def sensors_data(self):
        return self._sensors_dict

    def stop(self):
        self._logger.debug(f"Stopping gaiaSensors for {self._ecosystem}")
        self._stop_sensors_loop()
        self._logger.debug(f"gaiaSensors has been stopped for {self._ecosystem}")







