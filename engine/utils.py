# -*- coding: utf-8 -*-

from math import log, e

def pin_translation(pin, direction):
    """Tool to translate Raspberry Pi pin number
    Translates Raspberry Pi pin numbering from BCM number to board number 
    and vice versa
    
    :param pin: int, number of the pin to translate
    :param direction : str, either 'to_BCM' or 'to_board'

    :return int, the translated pin number
    """
    assert direction in ["to_BCM", "to_board"]

    to_BCM = {3: 2,
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

    to_board = {2: 3,
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

    if direction == "to_BCM":
        return to_BCM[pin]

    elif direction =="to_board":
        return to_board[pin]

def dew_point(temp, hum, precision_digit=2):
    """
    Returns the dew point temperature calculated using the Magnus formula.
    It uses the Sonntag1990 parameters which is valid from -45°C to 60°C
    ---
    :param temp: temperature in degree celsius
    
    :param hum: relative humidity in percent
    
    :param precision_digit: level of precision to keep in the result

    :return float, dew point temperature in celsius
    """
    b=17.62
    c=243.12
    al = log(hum/100) + (temp*b/(c + temp))
    Tdp = (c*al)/(b-al)
    return float(round(Tdp, precision_digit))

def absolute_humidity(temp, hum, precision_digit=2):
    """
    :param temp: temperature in degree celsius
    
    :param hum: relative humidity in percent
    
    :param precision_digit: level of precision to keep in the result

    :return float, absolute humidity in gram per cubic meter
    """
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
    x = 6.112 * (e**((17.67*temp)/(temp+243.5))*hum*2.1674)/(273.15+temp)
    return float(round(x, precision_digit))

def temperature_converter(temp, unit_in, unit_out, precision_digit=2):
    """
    :param temp: temperature
    
    :param unit_in: unit among Celsius, Kelvin, Fahrenheit (with or without
                    capital letter, can be abbreviated to the first letter)

    :param unit_out: unit among Celsius, Kelvin, Fahrenheit (with or without
                     capital letter, can be abbreviated to the first letter)
    
    :param precision_digit: level of precision to keep in the result

    :return float, the temperature converter into the desired unit
    """
    celsius = ["C", "c", "Celsius", "celsius"]
    kelvin = ["K", "k", "Kelvin", "kelvin"]
    fahrenheit = ["F", "f", "Fahrenheit", "fahrenheit"]
    K = 273.15
    if unit_in == unit_out:
        x = temp
    elif unit_in in celsius:
        if unit_out in kelvin:
            x = temp + K
        if unit_out in fahrenheit:
            x = temp*(9/5) + 32
    elif unit_in in kelvin:
        if unit_out in celsius:
            x = temp - K
        if unit_out in fahrenheit:
            x = (temp-K)*(9/5) + 32
    elif unit_in in fahrenheit:
        if unit_out in celsius:
            x = (temp-32)*(5/9)
        if unit_out in kelvin:
            x = (temp-32)*(5/9) + K
    return float(round(x, precision_digit))