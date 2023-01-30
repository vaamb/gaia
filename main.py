#!/usr/bin/python3
from setproctitle import setproctitle

setproctitle("gaia")

import eventlet

eventlet.monkey_patch()

from gaia import Gaia


if __name__ == "__main__":
    gaia = Gaia()
    try:
        gaia.start()
        gaia.wait()
    finally:
        gaia.stop()
