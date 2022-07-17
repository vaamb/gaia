import typing as t

from src.hardware.ABC import BaseSensor, PlantLevelHardware, Dimmer
from src.hardware import (
    ACTUATORS, GPIO_SENSORS, I2C_SENSORS, VIRTUAL_SENSORS
)

from .utils import (
    BASE_HARDWARE_DICT, GPIO_ADDRESS, HARDWARE_UID, I2C_ADDRESS, TEST_ADDRESS
)


if t.TYPE_CHECKING:
    from src.subroutines import Light


class TestHardware(BaseSensor, PlantLevelHardware):
    pass


def test_base_class(light_subroutine: "Light"):
    hardware_info = dict(BASE_HARDWARE_DICT[HARDWARE_UID])
    hardware_info["address"] = TEST_ADDRESS
    hardware = TestHardware(light_subroutine, HARDWARE_UID, **hardware_info)
    assert hardware.subroutine == light_subroutine
    assert hardware.uid == HARDWARE_UID
    assert hardware.name == "test"
    hardware.name = "foo"
    assert hardware.address == TEST_ADDRESS.replace("default", "0x0")
    assert hardware.level == "plants"
    assert hardware.model == "testModel"
    assert hardware.plant == "testPlant"
    assert "testMeasure" in hardware.measure
    str(hardware)
    assert isinstance(hardware.dict_repr, dict)


def test_actuators(light_subroutine: "Light"):
    hardware_info = dict(BASE_HARDWARE_DICT[HARDWARE_UID])
    hardware_info["address"] = GPIO_ADDRESS
    hardware_info["type"] = "light"
    hardware_info["level"] = "environment"
    for actuator_model, actuator_cls in ACTUATORS.items():
        hardware_info["model"] = actuator_model
        actuator = actuator_cls(light_subroutine, HARDWARE_UID, **hardware_info)
        actuator.turn_on()
        actuator.turn_off()
        if isinstance(actuator, Dimmer):
            actuator.set_pwm_level(15)
    return True


def test_gpio_sensors(light_subroutine: "Light"):
    hardware_info = dict(BASE_HARDWARE_DICT[HARDWARE_UID])
    hardware_info["address"] = GPIO_ADDRESS
    hardware_info["type"] = "sensor"
    hardware_info["level"] = "environment"
    hardware_info["measure"] = [
        "absolute_humidity", "dew_point", "humidity", "light", "temperature",
    ]
    for sensor_model, sensor_cls in GPIO_SENSORS.items():
        sensor = sensor_cls(light_subroutine, HARDWARE_UID, **hardware_info)
        sensor.get_data()
    return True


def test_i2c_sensors(light_subroutine: "Light"):
    hardware_info = dict(BASE_HARDWARE_DICT[HARDWARE_UID])
    hardware_info["address"] = I2C_ADDRESS
    hardware_info["type"] = "sensor"
    hardware_info["level"] = "environment"
    hardware_info["measure"] = [
        "absolute_humidity", "dew_point", "humidity", "light", "temperature",
    ]
    for sensor_model, sensor_cls in I2C_SENSORS.items():
        sensor = sensor_cls(light_subroutine, HARDWARE_UID, **hardware_info)
        sensor.get_data()
    return True


def test_virtual_sensors(light_subroutine: "Light"):
    hardware_info = dict(BASE_HARDWARE_DICT[HARDWARE_UID])
    hardware_info["type"] = "sensor"
    hardware_info["level"] = "environment"
    virtual_i2c_sensors = [f"virtual{model}" for model in I2C_SENSORS]
    for sensor_model, sensor_cls in VIRTUAL_SENSORS.items():
        if sensor_model in virtual_i2c_sensors:
            hardware_info["address"] = I2C_ADDRESS
        else:
            hardware_info["address"] = GPIO_ADDRESS
        hardware_info["model"] = sensor_model
        sensor = sensor_cls(light_subroutine, HARDWARE_UID, **hardware_info)
        sensor.get_data()
