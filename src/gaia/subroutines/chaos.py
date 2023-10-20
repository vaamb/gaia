from datetime import date, datetime, time, timedelta
from json.decoder import JSONDecodeError
from math import pi, sin
import typing as t
import random
import weakref

from gaia.utils import json


if t.TYPE_CHECKING:  # pragma: no cover
    from gaia.ecosystem import Ecosystem


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
            ecosystem: "Ecosystem",
            frequency: int = 10,
            max_duration: int = 10,
            max_intensity: float = 1.1,
    ) -> None:
        self._ecosystem: "Ecosystem" = weakref.proxy(ecosystem)
        self.frequency: int = frequency
        self.duration: int = max_duration
        self.intensity: float = max_intensity
        self._chaos_file = self.ecosystem.engine.config.cache_dir/"chaos.json"
        self._intensity_function: t.Callable = _intensity_function
        self._time_window: dict[str, datetime] = {}
        self._load_chaos()

    def _load_chaos(self) -> None:
        try:
            with self._chaos_file.open() as file:
                ecosystems = json.loads(file.read())
                params = ecosystems[self.ecosystem.uid]["time_window"]
                for param in ("start", "end"):
                    utc_param = datetime.fromisoformat(params[param])
                    self._time_window[param] = utc_param.astimezone()
        except (
                FileNotFoundError, JSONDecodeError, KeyError, ValueError
        ):
            now = datetime.now().astimezone()
            self._time_window = {
                "start": now - timedelta(seconds=2),
                "end": now - timedelta(seconds=1),
            }

    def _dump_chaos(self) -> None:
        try:
            with self._chaos_file.open("r") as file:
                ecosystems = json.loads(file.read())
        except (FileNotFoundError, JSONDecodeError):  # Empty file
            ecosystems = {}
        ecosystems[self.ecosystem.uid] = {
            "last_update": datetime.now().astimezone(),
            "time_window": self._time_window,
        }
        with self._chaos_file.open("w") as file:
            file.write(json.dumps(ecosystems))

    def update(self) -> None:
        now: datetime = datetime.now().astimezone()
        need_update: bool = False
        try:
            with self._chaos_file.open("r") as file:
                ecosystems: dict = json.loads(file.read())
                last_update_str: str = ecosystems[self.ecosystem.uid]["last_update"]
                last_update: datetime =\
                    datetime.fromisoformat(last_update_str).astimezone()
        except (FileNotFoundError, JSONDecodeError, KeyError):
            need_update = True
        else:
            if last_update.date() < now.date():
                need_update = True
        if all((need_update, now > self._time_window["end"], self.frequency)):
            chaos_probability = random.randint(1, self.frequency)
            if chaos_probability == 1:
                start = datetime.combine(date.today(), time()).astimezone()
                end = random.randint(1, self.duration)
                self._time_window["start"] = start
                self._time_window["end"] = start + timedelta(days=end)
            self._dump_chaos()

    @property
    def ecosystem(self):
        return self._ecosystem

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
            (self.intensity - 1.0)
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
