import random
from time import sleep

from config import Config

BASE_TEMPERATURE = 25
BASE_HUMIDITY = 60


def random_sleep(
        avg_duration: float = 0.15,
        std_deviation: float = 0.075
    ) -> None:
    if not Config.TESTING:
        sleep(abs(random.gauss(avg_duration, std_deviation)))


def get_temperature(*args, **kwargs) -> float:
    return random.gauss(BASE_TEMPERATURE, 2.5)


def get_humidity(*args, **kwargs) -> float:
    return random.gauss(BASE_HUMIDITY, 5)


def get_light(*args, **kwargs) -> float:
    return random.randrange(start=1000, stop=100000, step=10)


def get_moisture(*args, **kwargs) -> float:
    return random.randrange(start=10, stop=55)


def add_noise(measure):
    return measure * random.gauss(1, 0.01)
