from datetime import date, datetime, time, timedelta
import json
from math import pi, sin
from pathlib import Path
import typing as t
import random

from ..utils import base_dir, json


def _intensity_function(value: float) -> float:
    """ Function to calculate the max intensity value of the _time_window factor

    :param value: float: a value between 0 and 1
    :return float: a value between 0 and 1
    """
    value_radian = value * pi
    return sin(value_radian)


class Chaos:
    def __init__(
            self,
            frequency: int = 10,
            max_duration: int = 10,
            max_intensity: float = 1.1,
    ) -> None:
        self.frequency: int = frequency
        self.max_duration: int = max_duration
        self.max_intensity: float = max_intensity
        self.chaos_file: Path = base_dir / "cache" / "_time_window.json"
        self._intensity_function: t.Callable = _intensity_function
        self._time_window: dict[str, datetime] = {}
        self._load_chaos()
        self.update()

    def _load_chaos(self) -> None:
        try:
            with self.chaos_file.open() as file:
                params = json.loads(file.read())
                for param in ("start", "end"):
                    utc_param = datetime.fromisoformat(params[param])
                    self._time_window[param] = utc_param.astimezone()
        except (FileNotFoundError, ValueError):
            self._time_window = {
                "start": datetime.now().astimezone(),
                "end": datetime.now().astimezone(),
            }

    def _dump_chaos(self) -> None:
        with self.chaos_file.open("w") as file:
            file.write(json.dumps(self._time_window))

    def update(self) -> None:
        now = datetime.now().astimezone()
        need_update = False
        try:
            last_update = self.chaos_file.stat().st_mtime
            last_update = datetime.fromtimestamp(last_update)
        except FileNotFoundError:
            need_update = True
        else:
            if last_update.date() < now.date():
                need_update = True
        if all((need_update, now > self._time_window["end"], self.frequency)):
            chaos_probability = random.randint(1, self.frequency)
            if chaos_probability == 1:
                start = datetime.combine(date.today(), time()).astimezone()
                end = random.randint(1, self.max_duration)
                self._time_window["start"] = start
                self._time_window["end"] = start + timedelta(days=end)
            self._dump_chaos()

    @ property
    def status(self) -> bool:
        now = datetime.now().astimezone()
        return self._time_window["start"] <= now < self._time_window["end"]

    @property
    def factor(self) -> float:
        if not self.status or self.frequency == 0:
            return 1.0
        now = datetime.now().astimezone()
        chaos_duration = (self._time_window["end"] - self._time_window["start"])
        chaos_duration_minutes = chaos_duration.total_seconds() // 60
        chaos_start_to_now = (now - self._time_window["start"])
        chaos_start_to_now_minutes = chaos_start_to_now.total_seconds() // 60
        duration_fraction = chaos_start_to_now_minutes / chaos_duration_minutes
        return (
            self.intensity_function(duration_fraction) *
            (self.max_intensity - 1.0)
        ) + 1.0

    @property
    def intensity_function(self) -> t.Callable:
        return self._intensity_function

    @intensity_function.setter
    def intensity_function(self, function: t.Callable) -> None:
        self._intensity_function = function

    @property
    def time_window(self) -> dict:
        return self._time_window
