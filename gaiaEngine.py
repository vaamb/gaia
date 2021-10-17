#!/usr/bin/python
from setproctitle import setproctitle

setproctitle("gaiaEngine")

import eventlet

eventlet.monkey_patch()

import argparse
from time import sleep
import logging

from config import Config
from src.client import json, gaiaNamespace, retryClient
from src.manager import enginesManager
from src.utils import configure_logging


ADDR_TUPLE = Config.GAIAWEB
SERVER_URL = f"http://{ADDR_TUPLE[0]}:{ADDR_TUPLE[1]}"


logger = logging.getLogger("gaiaEngine")


class gaiaEngine:
    def __init__(self,
                 use_client: bool = False,
                 use_db: bool = False,
                 use_web_interface: bool = False,
                 ):
        logger.info("Initializing")
        self.use_client = use_client
        self.client = None
        self.started = False
        self.manager = enginesManager()

    def start(self):
        if not self.started:
            logger.info("Starting")
            self.manager.start(joint_start=True)
            if self.use_client:
                logger.info("Starting socketIO client")
                self.client = retryClient(json=json, logger=Config.DEBUG)
                namespace = gaiaNamespace(
                    engines_dict=self.manager.engines, namespace="/gaia"
                )
                self.client.register_namespace(namespace)
                self.manager.socketIO_client = self.client
                self.client.connect(SERVER_URL, transports="websocket", namespaces=['/gaia'])

            self.started = True
        else:
            raise RuntimeError("Only one instance of gaiaEngine can be run")

    def wait(self):
        if self.started:
            logger.info("Waiting ...")
            while True:
                if self.use_client:
                    self.client.sleep(1)
                else:
                    sleep(1)

    def stop(self):
        if self.started:
            logger.info("Stopping")
            self.manager.stop()
            self.client.disconnect()
            self.started = False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="gaiaEngine command line interface"
    )
    parser.add_argument("-c", "--client", action="store_true",
                        help="Activate socketIO client to communicate data "
                             "with gaiaWeb")
    parser.add_argument("-d", "--db", action="store_true",
                        help="Activate db data logging")
    parser.add_argument("-w", "--web", action="store_true",
                        help="Activate web interface")

    args = parser.parse_args()
    variables = vars(args)
    if any((Config.DEBUG, Config.TESTING)):
        variables["client"] = 1
        variables["db"] = 1
        variables["web"] = 1
    configure_logging(Config)
    gaia = gaiaEngine(use_client=variables["client"],
                      use_db=variables["db"],
                      use_web_interface=variables["web"]
                      )
    try:
        gaia.start()
        gaia.wait()
    finally:
        gaia.stop()
