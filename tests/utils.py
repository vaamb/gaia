ECOSYSTEM_UID = "zutqsCKn"

TESTING_ECOSYSTEM_CFG = {
    ECOSYSTEM_UID: {
        "name": "test",
        "status": False,
        "management": {
            "sensors": False,
            "light": False,
            "climate": False,
            "watering": False,
            "health": False,
            "alarms": False,
            "webcam": False,
        },
        "environment": {},
        "IO": {},
    }
}

TEST_ADDRESS = "I2C_0x20.default:GPIO_18"
I2C_ADDRESS = "I2C_default"
GPIO_ADDRESS = "GPIO_4:BOARD_12"

HARDWARE_UID = "cpgCZFJGGYlIXlLL"

BASE_HARDWARE_DICT = {
    HARDWARE_UID: {
        "name": "test",
        "address": "",
        "type": "sensor",
        "level": "plants",
        "model": "testModel",
        "plant": "testPlant",
        "measure": ["testMeasure"],
    },
}
