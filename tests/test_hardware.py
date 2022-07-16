from src.hardware.ABC import BaseSensor, PlantLevelHardware
from src.hardware.store import (
    ACTUATORS, GPIO_SENSORS, I2C_SENSORS, VIRTUAL_SENSORS
)

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


class TestHardware(BaseSensor, PlantLevelHardware):
    pass


def test_base_class(sensors_subroutine):
    hardware_info = dict(BASE_HARDWARE_DICT[HARDWARE_UID])
    hardware_info["address"] = TEST_ADDRESS
    hardware = TestHardware(sensors_subroutine, HARDWARE_UID, **hardware_info)
    assert hardware.subroutine == sensors_subroutine
    assert hardware.uid == HARDWARE_UID
    assert hardware.name == "test"
    hardware.name = "foo"
    assert hardware.address == TEST_ADDRESS.replace("default", "0x0")
    assert hardware.level == "plants"
    assert hardware.model == "testModel"
    assert hardware.plant == "testPlant"
    assert "testMeasure" in hardware.measure
    str(hardware)
    hardware.dict_repr


def test_actuators(light_subroutine):
    hardware_dict = dict(BASE_HARDWARE_DICT)
    for actuator_model in ACTUATORS:
        hardware_dict[HARDWARE_UID]["address"] = GPIO_ADDRESS
        hardware_dict[HARDWARE_UID]["type"] = "light"
        hardware_dict[HARDWARE_UID]["level"] = "environment"
        hardware_dict[HARDWARE_UID]["model"] = actuator_model
        light = light_subroutine.add_hardware(hardware_dict)
        light.turn_on()
        light.turn_off()
    return True


def test_gpio_sensors(sensors_subroutine):
    hardware_dict = dict(BASE_HARDWARE_DICT)
    for sensor_model in GPIO_SENSORS:
        hardware_dict[HARDWARE_UID]["address"] = GPIO_ADDRESS
        hardware_dict[HARDWARE_UID]["type"] = "sensor"
        hardware_dict[HARDWARE_UID]["level"] = "environment"
        hardware_dict[HARDWARE_UID]["model"] = sensor_model
        if sensor_model == "DHT22":
            hardware_dict[HARDWARE_UID]["measure"] = [
                "temperature", "humidity", "dew_point", "absolute_humidity"
            ]
        sensor = sensors_subroutine.add_hardware(hardware_dict)
        sensor.get_data()
    return True


def test_i2c_sensors(sensors_subroutine):
    hardware_dict = dict(BASE_HARDWARE_DICT)
    for sensor_model in I2C_SENSORS:
        hardware_dict[HARDWARE_UID]["address"] = I2C_ADDRESS
        hardware_dict[HARDWARE_UID]["type"] = "sensor"
        hardware_dict[HARDWARE_UID]["level"] = "environment"
        hardware_dict[HARDWARE_UID]["model"] = sensor_model
        sensors_subroutine.add_hardware(hardware_dict)
        sensor = sensors_subroutine.add_hardware(hardware_dict)
        sensor.get_data()
    return True


def test_virtual_sensors(sensors_subroutine):
    hardware_dict = dict(BASE_HARDWARE_DICT)
    virtual_i2c_sensors = [f"virtual{model}" for model in I2C_SENSORS]
    for sensor_model in VIRTUAL_SENSORS:
        if sensor_model in virtual_i2c_sensors:
            address = I2C_ADDRESS
        else:
            address = GPIO_ADDRESS
        hardware_dict[HARDWARE_UID]["address"] = address
        hardware_dict[HARDWARE_UID]["type"] = "sensor"
        hardware_dict[HARDWARE_UID]["level"] = "environment"
        hardware_dict[HARDWARE_UID]["model"] = sensor_model
        sensors_subroutine.add_hardware(hardware_dict)
        sensor = sensors_subroutine.add_hardware(hardware_dict)
        sensor.get_data()
