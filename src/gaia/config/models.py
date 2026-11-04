from __future__ import annotations

from datetime import date
from enum import Enum
from typing import TypedDict

from pydantic import Field, field_validator, model_serializer, RootModel

import gaia_validators as gv


DEFAULT_PLACE = "home"


# ---------------------------------------------------------------------------
#   Errors
# ---------------------------------------------------------------------------
class ConfigValidationError(ValueError): ...


class OutdatedData(RuntimeError): ...


# ---------------------------------------------------------------------------
#   Enums
# ---------------------------------------------------------------------------
class ConfigType(Enum):
    ecosystems = "ecosystems.cfg"
    private = "private.cfg"


class CacheType(Enum):
    chaos = "chaos.json"


# ---------------------------------------------------------------------------
#   Global config models
# ---------------------------------------------------------------------------
class SunTimesCacheData(TypedDict):
    last_update: date
    data: gv.SunTimesDict


# ---------------------------------------------------------------------------
#   Private config models
# ---------------------------------------------------------------------------
class CoordinatesDict(TypedDict):
    latitude: float
    longitude: float


class PrivateConfigValidator(gv.BaseModel):
    places: dict[str, gv.Coordinates] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)


class PrivateConfigDict(TypedDict):
    places: dict[str, gv.Coordinates]
    units: dict[str, str]


# ---------------------------------------------------------------------------
#   Ecosystem config models
# ---------------------------------------------------------------------------
class EcosystemBaseUpdateDict(TypedDict):
    name: str
    status: bool


# Custom EnvironmentConfig and EcosystemConfig models as ecosystems.cfg uses
#  uid: anonymous config dicts rather than config lists
class EnvironmentConfigValidator(gv.BaseModel):
    chaos: gv.ChaosConfig = Field(default_factory=gv.ChaosConfig)
    nycthemeral_cycle: gv.NycthemeralCycleConfig = Field(
        default_factory=gv.NycthemeralCycleConfig, validation_alias="sky")
    climate: dict[gv.ClimateParameter, gv.AnonymousClimateConfig] = \
        Field(default_factory=dict)
    weather: dict[gv.WeatherParameter, gv.AnonymousWeatherConfig] = \
        Field(default_factory=dict)


class EnvironmentConfigDict(TypedDict):
    chaos: gv.ChaosConfigDict
    nycthemeral_cycle: gv.NycthemeralCycleConfigDict
    climate: dict[gv.ClimateParameter, gv.AnonymousClimateConfigDict]
    weather: dict[gv.WeatherParameter, gv.AnonymousWeatherConfigDict]


class EcosystemConfigValidator(gv.BaseModel):
    name: str
    status: bool = False
    management: gv.ManagementConfig = Field(default_factory=gv.ManagementConfig)
    environment: EnvironmentConfigValidator = Field(
        default_factory=EnvironmentConfigValidator)
    hardware: dict[str, gv.AnonymousHardwareConfig] = Field(default_factory=dict, validation_alias="IO")
    plants: dict[str, gv.AnonymousPlantConfig] = Field(default_factory=dict)


class EcosystemConfigDict(TypedDict):
    name: str
    status: bool
    management: gv.ManagementConfigDict
    environment: EnvironmentConfigDict
    hardware: dict[str, gv.AnonymousHardwareConfigDict]
    plants: dict[str, gv.AnonymousPlantConfigDict]


class RootEcosystemsConfigValidator(RootModel):
    root: dict[str, EcosystemConfigValidator]


# Custom models to shorten climate, hardware and plants configs when dumping to YAML
class RootClimateValidator(RootModel):
    root: dict[gv.ClimateParameter, gv.AnonymousClimateConfig]


class _SerializableMeasure(gv.Measure):
    @model_serializer
    def serialize_model(self) -> str:
        return f"{self.name}|{self.unit if self.unit is not None else ''}"


class _SerializableAnonymousHardwareConfig(gv.AnonymousHardwareConfig):
    measures: list[_SerializableMeasure] = Field(default_factory=list, validation_alias="measure")


class RootHardwareValidator(RootModel):
    root: dict[str, _SerializableAnonymousHardwareConfig]


class RootPlantsValidator(RootModel):
    root: dict[str, gv.AnonymousPlantConfig]


class AnonymousHardwareConfigDictInput(TypedDict):
    name: str
    active: bool
    address: str
    type: str | gv.HardwareType
    level: str | gv.HardwareLevel
    groups: list[str] | set[str] | None
    model: str
    measures: list[str | gv.MeasureDict] | None
    plants: list[str] | None
    multiplexer_model: str | None


class HardwareConfigDictInput(AnonymousHardwareConfigDictInput):
    uid: str


# ---------------------------------------------------------------------------
#   Ecosystem chaos models
# ---------------------------------------------------------------------------
class ChaosMemory(TypedDict):
    last_update: date
    time_window: gv.TimeWindowDict


class ChaosMemoryValidator(gv.BaseModel[ChaosMemory]):
    last_update: date = Field(default_factory=date.today)
    time_window: gv.TimeWindow = gv.TimeWindow()

    @field_validator("last_update", mode="before")
    def parse_last_update(cls, value):
        if isinstance(value, str):
            return date.fromisoformat(value)
        return value


class ChaosMemoryRootValidator(RootModel):
    root: dict[str, ChaosMemoryValidator]
