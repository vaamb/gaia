# -*- coding: utf-8 -*-

def pin_translation(pin, direction):
    """Tool to translate Raspberry Pi pin number
    Translates Raspberry Pi pin numbering from BCM number to board nupmber 
    and vice versa
    
    :param pin: int, number of the pin to translate
    :param direction : str, either 'to_BCM' or 'to_board'

    :return int, translated pin number
    """
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