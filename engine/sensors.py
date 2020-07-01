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
from datetime import datetime
import pytz

from apscheduler.schedulers.background import BackgroundScheduler
#import RPi.GPIO as GPIO
#import Adafruit_DHT as dht
#import board
#import busio
#import adafruit_veml7700

from .config import gaiaConfig
from .utils import pin_translation

#only for dvlpmt:
import random

class gaiaSensors():
    def __init__(self, ecosystem):
        self.ecosystem = ecosystem
        self.config = gaiaConfig(self.ecosystem)
        self.logger = logging.getLogger("eng.{}.Sensors".format(self.ecosystem))
        self.logger.info("Initializing gaiaSensors for {}".format(self.ecosystem))
        self.timezone = self.config.local_timezone
        self._hardware_setup()
        self._create_sensors_dict()
        self.sensors_routine()
        self._scheduler()
#        self._sensors_config()
        self.logger.info("gaiaSensors has been initialized for {}".format(self.ecosystem))

    """Setup functions"""
    def _sensors_config(self):
        for sensor_type in ["environment", "plant"]:
            for sensor in self.config.get_sensor_list(sensor_type):
                pass

    def _create_sensors_dict(self):
        #Create the dictionnary that will hold the values read by the sensors
        #Create the base of the dictionnary
        self.sensors_dict = {"datetime": ""}
    
        #Add environmental sensors if there are any
        environmental_measures = self.config.get_measure_list("environment")
        if environmental_measures != []:
            environmental_measures = set(environmental_measures)
            self.sensors_dict["environment"] = {}
            for measure in environmental_measures:
                self.sensors_dict["environment"][measure] = {}
        
        #Add plants with sensors if there are any
        plants_with_sensors = self.config.plants_with_sensor
        if plants_with_sensors != []:
            self.sensors_dict["plants"] = {}       
            for sensor in self.config.get_sensor_list("plant"):
                measure = self.config.sensor_dict[sensor]["measure"]
                plant = self.config.sensor_dict[sensor]["plant"]
                self.sensors_dict["plants"][plant] = {measure: {}}

    def _hardware_setup(self):
#        GPIO.setmode(GPIO.BOARD)
#        for sensor in self.config.get_sensor_list("environment"):
            
            #General sensor setup
#            if sensor not in ["DHT22", "VEML7700"]:
#                pin = self.config.get_hardware_pin(sensor)
#               GPIO.setup(self.pin_light, GPIO.IN)
#                if sensor in ["myLowSensors"]:
#                    GPIO.output(pin, GPIO.LOW)
#                elif sensor in ["myHighSensors"]:
#                    GPIO.output(pin, GPIO.HIGH)
        pass

    """Background jobs"""
    def _scheduler(self):
        sched = BackgroundScheduler(daemon = True)
        sched.add_job(self.sensors_routine, "cron", second = "*/20", misfire_grace_time = 5)
        self.logger.info("Starting gaiaSensors background scheduler for {}"
                         .format(self.ecosystem))
        sched.start()
        self.logger.info("gaiaSensors background scheduler started for {}"
                         .format(self.ecosystem))

    def sensors_routine(self):
        """
        Loops through all the sensors and stores the value in self.sensors_dict
        """
        self.logger.debug("gaiaSensors starting the sensors routine for {}"
                          .format(self.ecosystem))
                
        #Set time
        now = datetime.now().replace(microsecond = 0)
        now_tz = now.astimezone(pytz.timezone(self.timezone))
        self.sensors_dict["datetime"] = now_tz
        
        #Loop through environmental sensors
        for sensor in self.config.get_sensor_list("environment"):
            pin = self.config.get_hardware_pin(sensor)

            #Support for DHT22
            if self.config.get_hardware_model(sensor) == "DHT22":
                pin_BCM = pin_translation(pin, "to_BCM")
#                humidity, temperature = dht.read_retry(dht.DHT22, pin_BCM)

                #For dvlpmnt only
                humidity = random.uniform(30, 65)
                temperature = random.uniform(17, 25)

                humidity = round(humidity, 1)
                temperature = round(temperature, 1)
                self.sensors_dict["environment"]["humidity"][sensor] = humidity
                self.sensors_dict["environment"]["temperature"][sensor] = temperature

            #Support for VEML7700
            if self.config.get_hardware_model(sensor) == "VEML7700":

                #For dvlpment only
                light = random.randrange(1000, 25000, 10)

                self.sensors_dict["environment"]["light"][sensor] = light

            #For development purpose only
            if self.config.get_hardware_model(sensor) == "myMegaSensor":
                temperature_data = random.uniform(17, 30)
                humidity_data = random.uniform(20, 55)
                light_data = random.randrange(1000, 100000, 10)
                self.sensors_dict["environment"]["temperature"][sensor] = round(temperature_data, 1)
                self.sensors_dict["environment"]["humidity"][sensor] = round(humidity_data, 1)
                self.sensors_dict["environment"]["light"][sensor] = light_data

        #Loop through plant sensors
        for sensor in self.config.get_sensor_list("plant"):
            pin = self.config.get_hardware_pin(sensor)
            measure = self.config.sensor_dict[sensor]["measure"]
            plant = self.config.sensor_dict[sensor]["plant"]

            if self.config.get_hardware_model(sensor) == "moisture1":
                
                #For development only
                sensor_data = round(random.uniform(10, 40), 2)
                
                self.sensors_dict["plants"][plant][measure][sensor] = sensor_data

        self.logger.debug("gaiaSensors finished the sensors routine for {}".format(self.ecosystem))


    """API calls"""
    #Configuration info
    def refresh_config(self):
        self.config = gaiaConfig(self.ecosystem)

    def refresh_hardware(self):
        self.refresh_config()
        self._setup_hardware()
        self._create_sensors_dict()

    #sensor
    @property
    def sensors_data(self):
        return self.sensors_dict


if __name__ == '__main__':
    from app.gaiaDatabase import gaiaDatabase
    database = gaiaDatabase()
    environment_frequency = 10
    plant_frequency = 15
    ecosystem = "B612"
    id = gaiaConfig().name_to_id_dict()[ecosystem]
    sensors = gaiaSensors(id)