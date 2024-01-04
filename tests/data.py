from datetime import time

import gaia_validators as gv


ecosystem_uid = "Rfrg5Kiv"
ecosystem_name = "Testing ecosystem"

hardware_address = "GPIO_10"
i2c_address = "I2C_default"
gpio_address = "GPIO_4:BOARD_12"


sensor_uid = "tKstp8EYJx27eQuK"
sensor_info = {
    "name": "VirtualSensor",
    "address": hardware_address,
    "model": "virtualDHT22",
    "type": "sensor",
    "level": "environment",
    "measures": ["temperature", "humidity"],
    "plants": [],
}


light_uid = "cpgCZFJGGYlIXlLL"
light_info = {
    "name": "VirtualLight",
    "address": "GPIO_7:GPIO_12",
    "model": "gpioDimmable",
    "type": "light",
    "level": "environment",
    "measures": [],
    "plants": [],
}


heater_uid = "A0oZpCJ50D0ajfJs"
heater_info = {
    "name": "VirtualHeater",
    "address": "GPIO_37:GPIO_10",
    "model": "gpioDimmable",
    "type": "heater",
    "level": "environment",
    "measures": [],
    "plants": [],
}


hardware_uid = sensor_uid
hardware_info = sensor_info


sun_times = {
    "twilight_begin": time(6, 15),
    "sunrise": time(7, 0),
    "sunset": time(20, 0),
    "twilight_end": time(20, 45),
}

lighting_start = time(8,00)
lighting_stop = time(20, 00)


ecosystem_info = {
    ecosystem_uid: {
        "name": ecosystem_name,
        "status": False,
        "management": {
            "sensors": False,
            "light": False,
            "climate": False,
            "watering": False,
            "health": False,
            "alarms": False,
            "pictures": False,
            "database": False,
        },
        "environment": {
            "chaos": {
                "frequency": 0,
                "duration": 0,
                "intensity": 0.0,
            },
            "sky": {
                "day": lighting_start,
                "night": lighting_stop,
                "lighting": gv.LightMethod.fixed,
            },
            "climate": {},
        },
        "IO": {
            light_uid: light_info,
            heater_uid: heater_info,
            sensor_uid: sensor_info,
        },
    },
}
