import typing as t
from typing import Type

from gaia.hardware.abc import BaseSensor, OneWireHardware
from gaia.hardware.sensors.abc import TemperatureSensor
from gaia.hardware.utils import is_raspi


if t.TYPE_CHECKING:
    if is_raspi():
        from gaia.hardware.sensors._devices.gaia_bs18b20 import BS18B20
    else:
        from gaia.hardware._compatibility import BS18B20 as _BS18B20


class BS18B20(OneWireHardware, TemperatureSensor):
    def _get_device(self) -> "BS18B20":
        if is_raspi():
            from gaia.hardware.sensors._devices.gaia_bs18b20 import BS18B20 as _BS18B20
        else:
            from gaia.hardware._compatibility import BS18B20 as _BS18B20
        return _BS18B20()

    def _get_raw_data(self) -> tuple[float | None]:
        return self.device.get_data()


onewire_sensor_models: dict[str, Type[BaseSensor]] = {
    hardware.__name__: hardware
    for hardware in [
        BS18B20,
    ]
}
