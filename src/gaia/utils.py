from __future__ import annotations

import base64
from enum import Enum
from datetime import date, datetime, time, timezone
from math import log, e
import os
import platform
import socket
from typing import Any
from weakref import WeakValueDictionary

import ruamel.yaml
from ruamel.yaml import SafeRepresenter, ScalarNode

from gaia.config import get_config


yaml = ruamel.yaml.YAML()


def _repr_time(self: SafeRepresenter, data: time) -> ScalarNode:
    time_repr = str(data).split(".")[0]
    return self.represent_scalar('tag:yaml.org,2002:str', time_repr)


def _repr_enum(self: SafeRepresenter, data: Enum) -> ScalarNode:
    return self.represent_scalar('tag:yaml.org,2002:str', data.value)


ruamel.yaml.add_representer(time, _repr_time, yaml.representer)
ruamel.yaml.add_multi_representer(Enum, _repr_enum, yaml.representer)
yaml.Constructor = ruamel.yaml.constructor.SafeConstructor


try:
    import orjson
except ImportError:
    import json as _json

    class datetimeJSONEncoder(_json.JSONEncoder):
        def default(self, obj: date | datetime | time) -> str:
            if isinstance(obj, datetime):
                return obj.astimezone(tz=timezone.utc).isoformat(timespec="seconds")
            if isinstance(obj, date):
                return obj.isoformat()
            if isinstance(obj, time):
                return (
                    datetime.combine(date.today(), obj)
                    .astimezone(tz=timezone.utc)
                    .isoformat(timespec="seconds")
                )

    class json:

        def dumps(*args, **kwargs) -> str:
            if 'cls' not in kwargs:
                kwargs['cls'] = datetimeJSONEncoder
            return _json.dumps(*args, **kwargs)

        @staticmethod
        def loads(*args, **kwargs) -> Any:
            return _json.loads(*args, **kwargs)

else:
    class json:

        def dumps(*args, **kwargs) -> str:
            return orjson.dumps(*args, **kwargs).decode("utf8")

        @staticmethod
        def loads(*args, **kwargs) -> Any:
            return orjson.loads(*args, **kwargs)


pin_board_to_bcm = {
    3: 2,
    5: 3,
    7: 4,
    8: 14,
    10: 15,
    11: 17,
    12: 18,
    13: 27,
    15: 22,
    16: 23,
    18: 24,
    19: 10,
    21: 9,
    22: 25,
    23: 11,
    24: 8,
    26: 7,
    27: 0,
    28: 1,
    29: 5,
    31: 6,
    32: 12,
    33: 13,
    35: 19,
    36: 16,
    37: 26,
    38: 20,
    40: 21
}


pin_bcm_to_board = {
    2: 3,
    3: 5,
    4: 7,
    14: 8,
    15: 10,
    17: 11,
    18: 12,
    27: 13,
    22: 15,
    23: 16,
    24: 18,
    10: 19,
    9: 21,
    25: 22,
    11: 23,
    8: 24,
    7: 26,
    0: 27,
    1: 28,
    5: 29,
    6: 31,
    12: 32,
    13: 33,
    19: 35,
    16: 36,
    26: 37,
    20: 38,
    21: 40
}


def utc_time_to_local_time(utc_time: time) -> time:
    dt = datetime.combine(date.today(), utc_time).replace(tzinfo=timezone.utc)
    return dt.astimezone().time()


def human_time_parser(human_time: str) -> time:
    """
    Returns the time from config file written in a human readable manner
    as a datetime.time object

    :param human_time: str, the time written in a 24h format, with hours
    and minutes separated by a 'h' or a 'H'. 06h05 as well as 6h05 or
    even 6H5 are valid input
    """
    hours, minutes = human_time.replace('H', 'h').split("h")
    return time(int(hours), int(minutes))


def humanize_list(lst: list) -> str:
    list_length = len(lst)
    if list_length == 0:
        return ""
    elif list_length == 1:
        return lst[0]
    else:
        return f"{', '.join(lst[:list_length-1])} and {lst[list_length-1]}"


def pin_translation(pin: int, direction: str) -> int:
    """Tool to translate Raspberry Pi pin number
    Translates Raspberry Pi pin numbering from BCM number to board number 
    and vice versa
    ---
    :param pin: int, number of the pin to translate
    :param direction: str, either 'to_BCM' or 'to_board'

    :return int, the translated pin number
    """
    assert direction in ["to_BCM", "to_board"]
    if direction == "to_BCM":
        return pin_board_to_bcm[pin]
    else:
        return pin_bcm_to_board[pin]


def get_dew_point(
        temp: float | None,
        hum: float | None,
        precision_digit: int = 2
) -> float | None:
    """
    Returns the dew point temperature calculated using the Magnus formula.
    It uses the Sonntag1990 parameters which is valid from -45°C to 60°C
    ---
    :param temp: temperature in degree celsius
    :param hum: relative humidity in percent
    :param precision_digit: level of precision to keep in the result

    :return float, dew point temperature in celsius
    """
    if temp is None or hum is None:
        return None
    if hum == 0.0:
        hum = 0.1
    b = 17.62
    c = 243.12
    al = log(hum / 100) + (temp * b / (c + temp))
    Tdp = (c * al) / (b - al)

    return float(round(Tdp, precision_digit))


def get_absolute_humidity(
        temp: float | None,
        hum: float | None,
        precision_digit: int = 2
) -> float | None:
    """
    Calculates the absolute humidity. The formula used is given below
    :param temp: temperature in degree celsius
    :param hum: relative humidity in percent
    :param precision_digit: level of precision to keep in the result

    :return float, absolute humidity in gram per cubic meter
    """
    if temp is None or hum is None:
        return None
    # The formula is based on ideal gas law (PV = nRT) where n = m/M and V = 1m**3
    # As we need m, we transform it to m = PVM/RT
    # Pressure of water vapor at 100% relative humidity:
    # psat = 6.112 * e**((17.67 * temp)/(temp + 243.5))
    # Pressure at hum%relative humidity
    # p = psat * (hum/100)
    # Molar weight of water
    # Mwater = 18.02
    # Gas constant (here we want the result in grams, not kg so we divide it by 1000)
    # R = 0.08314 
    # result = (p*Mwater)/(R*(Temp+273.15))
    # Or simplified:
    x = 6.112 * (e ** ((17.67 * temp) / (temp + 243.5)) * hum * 2.1674) / (273.15 + temp)
    return float(round(x, precision_digit))


def temperature_converter(
        temp: float | None,
        unit_in: str,
        unit_out: str,
        precision_digit: int = 2
) -> float | None:
    """
    :param temp: float, the temperature in Celsius degrees
    :param unit_in: str, unit among Celsius, Kelvin, Fahrenheit (with or without
                    capital letter, can be abbreviated to the first letter)
    :param unit_out: str, unit among Celsius, Kelvin, Fahrenheit (with or without
                     capital letter, can be abbreviated to the first letter)
    :param precision_digit: int, level of precision to keep in the result

    :return float, the temperature converter into the desired unit
    """
    if temp is None:
        return None
    celsius = ["c", "celsius"]
    kelvin = ["k", "kelvin"]
    fahrenheit = ["f", "fahrenheit"]
    K = 273.15

    if unit_in.lower() == unit_out.lower():
        return temp

    elif unit_in.lower() in celsius:
        if unit_out.lower() in kelvin:
            x = temp + K
        elif unit_out.lower() in fahrenheit:
            x = temp * (9 / 5) + 32
        else:
            raise ValueError(
                "units must be 'celsius', 'fahrenheit' or 'kelvin'"
            )

    elif unit_in.lower() in kelvin:
        if unit_out.lower() in celsius:
            x = temp - K
        elif unit_out.lower() in fahrenheit:
            x = (temp - K) * (9 / 5) + 32
        else:
            raise ValueError(
                "units must be 'celsius', 'fahrenheit' or 'kelvin'"
            )

    elif unit_in.lower() in fahrenheit:
        if unit_out.lower() in celsius:
            x = (temp - 32) * (5 / 9)
        elif unit_out.lower() in kelvin:
            x = (temp - 32) * (5 / 9) + K
        else:
            raise ValueError(
                "units must be 'celsius', 'fahrenheit' or 'kelvin'"
            )

    else:
        raise ValueError(
            "units must be 'celsius', 'fahrenheit' or 'kelvin'"
        )

    return float(round(x, precision_digit))


def get_unit(measure: str, default: str) -> str:
    from gaia.config.from_files import EngineConfig
    return EngineConfig().units.get(measure, default)


def is_connected() -> bool:
    _is_connected = False
    host = get_config().TEST_CONNECTION_IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    try:
        s.connect((host, 80))
        _is_connected = True
    finally:
        s.close()
    return _is_connected


def local_ip_address() -> str:
    address = "127.0.0.1"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.1)
    try:
        s.connect(("192.168.0.1", 80))
        address = s.getsockname()[0]
    finally:
        s.close()
    return address


def generate_secret_key_from_password(
        password: str | bytes,
        set_env: bool = False
) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    if isinstance(password, str):
        password = password.encode("utf-8")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"",
        iterations=2**21,
    )
    bkey = kdf.derive(password)
    skey = base64.b64encode(bkey).decode("utf-8").strip("=")
    if set_env:
        if platform.system() in ("Linux", "Windows"):
            os.environ["GAIA_SECRET_KEY"] = skey
        else:
            # Setting environ in BSD and MacOsX can lead to mem leak (cf. doc)
            os.putenv("GAIA_SECRET_KEY", skey)
    return skey


class SingletonMeta(type):
    _instances: dict[type, type] = WeakValueDictionary()

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        return cls._instances[cls]
