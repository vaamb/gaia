import glob
import os
import time
from typing import TypeAlias


RawData: TypeAlias = tuple[list[tuple[str, float]], list[str]]


class BS18B20:
    def __init__(self, addr: str | None = None) -> None:
        # Get one wire devices
        device_dirs: list[str]
        if addr:
            device_dirs = [f"/sys/bus/w1/devices/{addr}"]
        else:
            device_dirs = glob.glob('/sys/bus/w1/devices/28*')
        if not device_dirs:
            raise RuntimeError("No device found.")
        self.device_dirs = device_dirs

    @staticmethod
    def _try_get_single_data(device_dir: str) -> float:
        # The w1_slave file contains the sensor data
        # The first line contains binary followed by the status of the sensor
        # The second line contains binary followed by the temperature data
        try:
            with open(device_dir, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            raise RuntimeError("Device not found.")
        if len(lines) < 2:
            raise RuntimeError("Invalid data from device.")
        if lines[0].strip()[-3:] != 'YES':
            raise RuntimeError("Device is not ready.")
        # Temperature data is stored as ´t=´ followed by the temperature in milli degrees
        equals_pos = lines[1].find('t=')
        if equals_pos == -1:
            raise RuntimeError("Invalid data from device.")
        # Return temperature in degrees
        return float(lines[1][equals_pos + 2:]) / 1000.0

    def _try_get_all_data(self, device_dirs: list[str]) -> RawData:
        successful: list[tuple[str, float]] = []
        failed: list[str] = []
        for device_dir in device_dirs:
            try:
                successful.append((device_dir, self._try_get_single_data(device_dir)))
            except RuntimeError:
                failed.append(device_dir)
        return successful, failed

    def get_data(self) -> float | None:
        device_files: list[str] = [
            os.path.join(device_dir, "w1_slave")
            for device_dir in self.device_dirs
        ]
        result: list[float] = []
        retries = 3
        while True:
            successful, failed = self._try_get_all_data(device_files)
            result.extend([data[1] for data in successful])
            if not failed:
                break
            retries -= 1
            if retries == 0:
                break
            device_files = failed
            time.sleep(0.2)
        if not result:
            return None
        return sum(result) / len(result)
