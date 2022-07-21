#!/usr/bin/python
from setproctitle import setproctitle

setproctitle("Gaia")

import eventlet

eventlet.monkey_patch()

import argparse

from config import Config
from src import Gaia
from src.utils import configure_logging


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Gaia command line interface"
    )
    parser.add_argument("-c", "--socketio", action="store_true",
                        help="Activate socketIO client to communicate data "
                             "with Ouranos")
    parser.add_argument("-d", "--db", action="store_true",
                        help="Activate db data logging locally")

    args = parser.parse_args()
    variables = vars(args)
    if any((Config.DEBUG, Config.TESTING)):
        variables["socketio"] = True
        variables["db"] = True
    configure_logging(Config)
    gaia = Gaia(
        connect_to_ouranos=variables["socketio"],
        use_database=variables["db"],
    )
    try:
        gaia.start()
        gaia.wait()
    finally:
        gaia.stop()
