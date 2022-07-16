ECOSYSTEM_UID = "zutqsCKn"

TESTING_ECOSYSTEM_CFG = {
    ECOSYSTEM_UID: {
        "name": "test",
        "status": True,
        "management": {
            "sensors": True,
            "light": True,
            "climate": True,
            "watering": True,
            "health": True,
            "alarms": True,
            "webcam": True,
        },
        "environment": {
            "light": "fixed"
        },
        "IO": {},
    }
}