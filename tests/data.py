from datetime import time

import gaia_validators as gv


place_name = "home"
place_longitude = 42.0
place_latitude = 7.0


engine_uid = "engine_uid"

ecosystem_uid = "Rfrg5Kiv"
ecosystem_name = "Testing ecosystem"

hardware_address = "GPIO_19"
i2c_address = "I2C_default"
gpio_address = "GPIO_4:BOARD_12"


sensor_uid = "tKstp8EYJx27eQuK"
sensor_info = {
    "name": "VirtualSensor",
    "address": hardware_address,
    "model": "virtualDHT22",
    "type": gv.HardwareType.sensor,
    "level": gv.HardwareLevel.environment,
    "measures": ["temperature", "humidity"],
    "plants": [],
}


light_uid = "cpgCZFJGGYlIXlLL"
light_info = {
    "name": "VirtualLight",
    "address": "GPIO_5&GPIO_13",
    "model": "gpioDimmable",
    "type": gv.HardwareType.light,
    "level": gv.HardwareLevel.environment,
    "measures": [],
    "plants": [],
}


heater_uid = "A0oZpCJ50D0ajfJs"
heater_info = {
    "name": "VirtualHeater",
    "address": "GPIO_26&GPIO_12",
    "model": "gpioDimmable",
    "type": gv.HardwareType.heater,
    "level": gv.HardwareLevel.environment,
    "measures": [],
    "plants": [],
}


camera_uid = "aVxKrtCOQHeu8GpN"
camera_info = {
    "name": "Camera",
    "address": "PICAMERA",
    "model": "PiCamera",
    "type": gv.HardwareType.camera,
    "level": gv.HardwareLevel.environment,
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

lighting_start = time(8, 00)
lighting_stop = time(20, 00)
lighting_method = gv.LightingMethod.fixed


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
            "nycthemeral_cycle": {
                "day": lighting_start,
                "night": lighting_stop,
                "lighting": lighting_method,
            },
            "climate": {},
        },
        "IO": {
            light_uid: light_info,
            heater_uid: heater_info,
            sensor_uid: sensor_info,
            camera_uid: camera_info,
        },
    },
}
