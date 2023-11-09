from datetime import time

import gaia_validators as gv


ecosystem_uid = "Rfrg5Kiv"
ecosystem_name = "Testing ecosystem"

i2c_address = "I2C_default"
gpio_address = "GPIO_4:BOARD_12"

hardware_uid = "cpgCZFJGGYlIXlLL"
hardware_name = "TestingHardware"
hardware_address = "GPIO_10"

hardware_info = {
    "name": hardware_name,
    "address": hardware_address,
    "model": "gpioSwitch",
    "type": "light",
    "level": "environment",
    "measures": ["testMeasure"],
    "plants": ["testPlant"],
}

sun_times = {
    "twilight_begin": time(6, 15),
    "sunrise": time(7, 0),
    "sunset": time(20, 0),
    "twilight_end": time(20, 45),
}

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
                "day": time(8,00),
                "night": time(20, 00),
                "lighting": gv.LightMethod.fixed,
            },
            "climate": {},
        },
        "IO": {
            hardware_uid: hardware_info
        },
    },
}
