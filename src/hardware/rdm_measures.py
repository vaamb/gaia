import random


BASE_TEMPERATURE = 25
BASE_HUMIDITY = 60


def get_temperature(*args, **kwargs) -> float:
    return random.gauss(BASE_TEMPERATURE, 2.5)


def get_humidity(*args, **kwargs) -> float:
    return random.gauss(BASE_HUMIDITY, 5)


def get_light(*args, **kwargs) -> float:
    return random.randrange(1000, 100000, 10)


def get_moisture(*args, **kwargs) -> float:
    return random.randrange(35, 10)


def add_noise(measure):
    return measure * random.gauss(1, 0.01)
