from __future__ import annotations

from gaia.hardware._compatibility import busio


class TCA9548A_Channel(busio.I2C):
    def __init__(self, tca: TCA9548ADevice, channel: int) -> None:
        super().__init__()
        self.tca = tca
        self.channel_switch = bytearray([1 << channel])


class TCA9548ADevice:
    def __init__(self, i2c: busio.I2C, address: int = 0x70):
        self.i2c = i2c
        self.address = address
        self.channels: list[TCA9548A_Channel | None] = [None] * 8

    def __len__(self) -> int:
        return 8

    def __getitem__(self, key: int) -> TCA9548A_Channel:
        if not 0 <= key <= 7:
            raise IndexError("Channel must be an integer in the range: 0-7.")
        channel = self.channels[key]
        if channel is None:
            channel = TCA9548A_Channel(self, key)
            self.channels[key] = channel
        return channel
