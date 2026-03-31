from __future__ import annotations

import typing as t
from typing import Type

from gaia.hardware.abc import Sensor, OneWireAddressMixin
from gaia.hardware.sensors.abc import TemperatureSensor
from gaia.hardware.utils import is_raspi


if t.TYPE_CHECKING:
    if is_raspi():
        from gaia.hardware.sensors._devices.gaia_ds18b20 import DS18B20Device
    else:
        from gaia.hardware.sensors._devices._compatibility import DS18B20Device


class OneWireSensor(OneWireAddressMixin, Sensor):
    ...


class DS18B20(TemperatureSensor, OneWireSensor):
    def _get_device(self) -> DS18B20Device:
        if is_raspi():
            from gaia.hardware.sensors._devices.gaia_ds18b20 import DS18B20Device
        else:
            from gaia.hardware.sensors._devices._compatibility import DS18B20Device
        return DS18B20Device(self.device_address)

    def _get_raw_data(self) -> float | None:
        return self.device.get_data()


onewire_sensor_models: dict[str, Type[OneWireSensor]] = {
    hardware.__name__: hardware
    for hardware in [
        DS18B20,
    ]
}
