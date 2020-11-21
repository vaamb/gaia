#!/usr/bin/python
import eventlet

eventlet.monkey_patch()

from time import sleep

from client import json, gaiaNamespace, retryClient
from config import Config
from engine import autoManager, enginesDict


ADDR_TUPLE = Config.GAIAWEB
SERVER_URL = f"http://{ADDR_TUPLE[0]}:{ADDR_TUPLE[1]}"

# TODO: add CLI
class gaiaEngine:
    def __init__(self, use_client=True):
        self.use_client = use_client
        self.client = None
        self.started = False

    def start(self):
        if not self.started:
            autoManager.start(joint_start=True)
            if self.use_client:
                self.client = retryClient(json=json)
                namespace = gaiaNamespace(engines_dict=enginesDict, namespace="/gaia")
                self.client.register_namespace(namespace)
                self.client.connect(SERVER_URL, transports="websocket", namespaces=['/gaia'])
            self.started = True
        else:
            raise RuntimeError("Only one instance of gaiaEngine can be run")

    def wait(self):
        if self.started:
            while True:
                if self.use_client:
                    self.client.sleep(1)
                else:
                    sleep(1)

    def stop(self):
        if self.started:
            autoManager.stop()
            self.client.disconnect()
            self.started = False


if __name__ == "__main__":
    gaia = gaiaEngine()
    try:
        gaia.start()
        gaia.wait()
    finally:
        gaia.stop()
