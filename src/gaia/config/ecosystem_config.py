from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import logging
from math import pi, sin
import random
import sys
from typing import cast, Collection, NamedTuple, TYPE_CHECKING
from weakref import WeakValueDictionary

import pydantic
from pydantic import TypeAdapter

import gaia_validators as gv
from gaia_validators import safe_enum_from_name

from gaia.config import default_actuators
from gaia.config.default_actuators import ClimateDirection, Direction, WeatherDirection
from gaia.config.helpers import (
    closest_parameter_hint, format_pydantic_error, remove_missing, to_dt)
from gaia.config.models import (
    AnonymousHardwareConfigDictInput, CacheType, ChaosMemoryValidator, ConfigType,
    ConfigValidationError, DEFAULT_PLACE, EcosystemConfigDict, EnvironmentConfigDict,
    EnvironmentConfigValidator, HardwareConfigDictInput, OutdatedData)
from gaia.exceptions import HardwareNotFound, PlantNotFound, UndefinedParameter
from gaia.hardware import hardware_models, Hardware
from gaia.hardware.abc import SensorMixin
from gaia.hardware.multiplexers import multiplexer_models
from gaia.subroutines import subroutine_dict
from gaia.utils import create_uid, humanize_list, is_time_between

if sys.version_info < (3, 12):
    from typing_extensions import Unpack
else:
    from typing import Unpack

if TYPE_CHECKING:
    from gaia.config.engine_config import EngineConfig


class ConfigIssues(NamedTuple):
    errors: list  # The ecosystem is invalid and cannot start
    warnings: list  # The ecosystem will use fallback values


class ScaledClimateTarget(NamedTuple):
    day: float
    night: float
    hysteresis: float


DEFAULT_LIGHT_CLIMATE_CFG = gv.ClimateConfig(**{  # ty: ignore[invalid-argument-type]
    "parameter": "light",
    "day": 250_000,
    "night": -30_000,
    "hysteresis": 0.0,
})


parameter_adapter = TypeAdapter(gv.EnvironmentParameter)


class _MetaEcosystemConfig(type):
    instances: WeakValueDictionary[str, EcosystemConfig] = WeakValueDictionary()

    def __call__(cls, *args, **kwargs) -> EcosystemConfig:
        try:
            ecosystem_id = kwargs["ecosystem_id"]
        except KeyError:
            try:
                ecosystem_id = args[0]
            except IndexError:  # pragma: no cover
                raise TypeError(
                    "EcosystemConfig() missing 1 required argument: 'ecosystem_id'"
                )
        from gaia.config.engine_config import EngineConfig

        engine_config = EngineConfig()
        if not engine_config.configs_loaded:
            raise RuntimeError(
                "Configuration files need to be loaded by `EngineConfig` in "
                "order to get an `EcosystemConfig` instance. To do so, use the "
                "`EngineConfig().initialize_configs()` method."
            )
        ecosystem_uid = engine_config.get_IDs(ecosystem_id).uid
        try:
            return cls.instances[ecosystem_uid]
        except KeyError:
            ecosystem_config: EcosystemConfig = cast(
                EcosystemConfig, cls.__new__(cls, ecosystem_uid, *args, **kwargs))
            ecosystem_config.__init__(*args, **kwargs)
            cls.instances[ecosystem_uid] = ecosystem_config
            return ecosystem_config


class EcosystemConfig(metaclass=_MetaEcosystemConfig):
    def __init__(
            self,
            ecosystem_id: str,
            engine_config: EngineConfig | None = None,
    ) -> None:
        from gaia.config.engine_config import EngineConfig

        engine_config = engine_config or EngineConfig()
        self._engine_config: EngineConfig = engine_config
        ids = self._engine_config.get_IDs(ecosystem_id)
        self.uid = ids.uid
        name = ids.name.replace(" ", "_")
        self.logger = logging.getLogger(f"gaia.engine.{name}.config")
        self._nycthemeral_span_method: gv.NycthemeralSpanMethod | None = None
        self._nycthemeral_span_hours: gv.NycthemeralSpanConfig | None = None
        self._lighting_method: gv.LightingMethod | None = None
        self._lighting_hours: gv.LightingHours | None = None

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"{self.__class__.__name__}({self.uid}, name={self.name}, "
            f"engine_config={self._engine_config})"
        )

    # ---------------------------------------------------------------------------
    #   Properties and common utilities
    # ---------------------------------------------------------------------------
    @property
    def _config_dict(self) -> EcosystemConfigDict:
        return self._engine_config.ecosystems_config_dict[self.uid]

    @_config_dict.setter
    def _config_dict(self, value: EcosystemConfigDict) -> None:
        self._engine_config.ecosystems_config_dict[self.uid] = value

    @property
    def general(self) -> EngineConfig:
        return self._engine_config

    @property
    def name(self) -> str:
        return self._config_dict["name"]

    @name.setter
    def name(self, value: str) -> None:
        self._config_dict["name"] = value

    @property
    def status(self) -> bool:
        return self._config_dict["status"]

    @status.setter
    def status(self, value: bool) -> None:
        self._config_dict["status"] = value

    async def save(self) -> None:
        """Persist the ecosystem configuration to the ecosystems.cfg file."""
        await self._engine_config.save(ConfigType.ecosystems)

    @classmethod
    def normalize_config_dict(
            cls,
            ecosystem_cfg: EcosystemConfigDict,
    ) -> EcosystemConfigDict:
        """Normalize a raw, structurally valid, ecosystem config dict.

        Used at load time, before any `EcosystemConfig` instance exists, to
        normalize the config to the form Gaia works with:

        - Each hardware dict is replaced by the result of
          `validate_hardware_dict()`, so its address is expanded to its
          normalized form: 'BOARD_7' becomes 'GPIO_4', a 'default' I2C address
          becomes the address of the model ...
        - A climate parameter without linked actuators gets the default ones,
          so they can be seen, changed or disabled from the config file.
        - A weather parameter without linked actuator gets the default one,
          if it has one.

        This step will also report unsupported models and invalid address.

        Note: the dict is modified in place and returned. If an error is
        raised, the valid hardware dicts have already been normalized.

        :param ecosystem_cfg: The ecosystem configuration dict to normalize.
        :return: The same ecosystem configuration dict, normalized.
        :raises ConfigValidationError: If some hardware dicts are invalid, with
                                       one error message per invalid hardware
                                       in `args`.
        """
        # Normalize hardware dicts
        errors: list[str] = []
        for hardware_uid, hardware_dict in ecosystem_cfg["hardware"].items():
            try:
                validated = cls.validate_hardware_dict(
                    gv.to_identified(hardware_dict, {"uid": hardware_uid}))
            except ValueError as e:
                errors.append(f"hardware '{hardware_dict['name']}': {e}")
            else:
                ecosystem_cfg["hardware"][hardware_uid] = gv.to_anonymous(validated, "uid")
        # Normalize climate dicts
        for parameter, climate_cfg in ecosystem_cfg["environment"]["climate"].items():
            if climate_cfg.get("linked_actuators") is None:
                climate_cfg["linked_actuators"] = \
                    cls.get_default_climate_linked_actuator(parameter)
        # Normalize weather dicts
        for parameter, weather_cfg in ecosystem_cfg["environment"]["weather"].items():
            if weather_cfg.get("linked_actuator") is None:
                weather_cfg["linked_actuator"] = \
                    cls.get_default_weather_linked_actuator(parameter)
        if errors:
            raise ConfigValidationError(*errors)
        return ecosystem_cfg

    @classmethod
    def check_config_logic(
            cls,
            ecosystem_cfg: EcosystemConfigDict,
            places: Collection[str],
    ) -> ConfigIssues:
        """Check the logic of a structurally valid, and normalized, ecosystem
        config dict.

        Used at load time, before any `EcosystemConfig` instance exists, to
        catch hardware and climate role errors that the structural validation
        cannot see. Soft issues, such as a nycthemeral method falling back to
        'fixed', are caught as warnings.

        :param ecosystem_cfg: The ecosystem configuration dict to check.
        :param places: Collection of available places with their coordinates.
        :return: A named tuple with error and warning messages.
        """
        warnings: list[str] = []
        errors: list[str] = []
        # Check hardware dicts (errors)
        addresses_used: list[str] = []
        for hardware_uid, hardware_cfg in ecosystem_cfg["hardware"].items():
            try:
                cls.check_hardware_address(hardware_cfg["address"], addresses_used)
            except ValueError as e:
                errors.append(f"Hardware '{hardware_cfg['name']}': {e}")
            finally:
                addresses_used.append(hardware_cfg["address"])
        # Check climate roles (errors)
        climate = ecosystem_cfg["environment"]["climate"]
        try:
            cls.check_climate_groups(climate)
        except ConfigValidationError as e:
            errors.append(str(e))
        for hardware_uid, hardware_cfg in ecosystem_cfg["hardware"].items():
            try:
                cls.check_hardware_groups(hardware_uid, hardware_cfg, climate)
            except ConfigValidationError as e:
                errors.append(str(e))
        # Check climate parameters (warnings)
        for parameter, climate_cfg in climate.items():
            try:
                cls.check_climate_sensors(parameter, climate_cfg, ecosystem_cfg["hardware"])
            except ConfigValidationError as e:
                warnings.append(f"{e} It will not be regulated.")
        # Check nycthemeral config (warnings)
        nycthemeral_cfg = ecosystem_cfg["environment"]["nycthemeral_cycle"]
        for method in (nycthemeral_cfg["span"], nycthemeral_cfg["lighting"]):
            try:
                cls.check_nycthemeral_method(method, ecosystem_cfg, places)
            except ConfigValidationError as e:
                warnings.append(f"{e} Will fall back to 'fixed'.")
        return ConfigIssues(errors, warnings)

    def reset_nycthemeral_caches(self) -> None:
        """Clear cached nycthemeral span and lighting values.

        Forces recomputation on next access.
        """
        self._nycthemeral_span_method = None
        self._nycthemeral_span_hours = None
        self._lighting_method = None
        self._lighting_hours = None

    def reset_caches(self) -> None:
        """Clear all cached configuration values."""
        self.reset_nycthemeral_caches()

    # ---------------------------------------------------------------------------
    #   Ecosystem management (subroutine and other capabilities)
    # ---------------------------------------------------------------------------
    @property
    def managements(self) -> gv.ManagementConfigDict:
        return self._config_dict["management"]

    def set_managements(self, **value: Unpack[gv.ManagementConfigDict]) -> None:
        """Set managements in the dict format

        :param value: The management config values with all the entries provided.
        """
        try:
            validated_values = gv.ManagementConfig(**value).model_dump()
        except pydantic.ValidationError as e:  # pragma: no cover
            raise ValueError(
                f"Invalid management parameters provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
        self._config_dict["management"] = validated_values

    @property
    def management_flag(self) -> int:
        management_config = gv.ManagementConfig(**self.managements)
        return management_config.to_flag()

    def get_management(
            self,
            management: str | gv.ManagementFlags,
    ) -> bool:
        """Check if a management capability is enabled.

        :param management: The management flag name or enum to check.
        :return: True if the management capability is enabled.
        """
        validated_management = safe_enum_from_name(gv.ManagementFlags, management)
        # If management has dependencies, load them
        if validated_management >= 256:
            base_name: str = validated_management.name
            dep = f"{validated_management.name}_enabled"
            validated_management = safe_enum_from_name(gv.ManagementFlags, dep)
            self.logger.debug(
                f"{base_name.upper()} management has dependencies. Checking for "
                f"{validated_management.name} ({validated_management.value}).")
        flag = self.management_flag
        return flag & validated_management == validated_management

    def set_management(
            self,
            management: str | gv.ManagementFlags,
            value: bool,
    ) -> None:
        """Enable or disable a management capability.

        :param management: The management flag name or enum to set.
        :param value: True to enable, False to disable.
        """
        validated_management = safe_enum_from_name(gv.ManagementFlags, management)
        management_name: str = validated_management.name
        if validated_management >= 256 and value:
            composite_name = f"{validated_management.name}_enabled"
            composite_mgmt = safe_enum_from_name(gv.ManagementFlags, composite_name)
            flag = self.management_flag
            if not flag & composite_mgmt == composite_mgmt:
                dep = gv.ManagementFlags(composite_mgmt - validated_management)
                self.logger.warning(
                    f"{management_name.upper()} management has unmet dependencies: "
                    f"{dep}. This might lead to issues if it is not enabled.")
        self._config_dict["management"][management_name] = value  # ty: ignore[invalid-key]

    def get_subroutines_enabled(self) -> list[str]:
        """Return the list of subroutine names that are enabled for this ecosystem."""
        return [
            subroutine
            for subroutine in subroutine_dict
            if self.get_management(subroutine)
        ]

    # ---------------------------------------------------------------------------
    #   Environment parameters
    # ---------------------------------------------------------------------------
    @property
    def environment(self) -> EnvironmentConfigDict:
        """
        Returns the environment config for the ecosystem
        """
        try:
            return self._config_dict["environment"]
        except KeyError:  # pragma: no cover
            self._config_dict["environment"] = EnvironmentConfigValidator().model_dump()
            return self._config_dict["environment"]

    # ---------------------------------------------------------------------------
    #      Nycthemeral cycle parameters
    # ---------------------------------------------------------------------------
    @property
    def nycthemeral_cycle(self) -> gv.NycthemeralCycleConfigDict:
        """
        Returns the nycthemeral cycle config for the ecosystem
        """
        try:
            return self.environment["nycthemeral_cycle"]
        except KeyError:  # pragma: no cover
            self.environment["nycthemeral_cycle"] = \
                gv.NycthemeralCycleConfig().model_dump()
            return self.environment["nycthemeral_cycle"]

    async def set_nycthemeral_cycle(
            self,
            **value: Unpack[gv.NycthemeralCycleConfigDict],
    ) -> None:
        """Set all nycthemeral cycle parameters at once.

        :param value: Nycthemeral cycle configuration with target, span, day,
                      night, and lighting keys.
        :raises ValueError: If the provided parameters are invalid.
        """
        try:
            validated_value: gv.NycthemeralCycleConfigDict = \
                gv.NycthemeralCycleConfig(**value).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid time parameters provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
        # Store a copy of the current config if the new one is later rejected
        previous = self.nycthemeral_cycle.copy()
        try:
            await self.set_nycthemeral_span_target(validated_value["target"], False)
            await self.set_nycthemeral_span_method(validated_value["span"], False)
            await self.set_nycthemeral_span_hours({
                "day": validated_value["day"], "night": validated_value["night"]}, False)
            await self.set_lighting_method(validated_value["lighting"], False)
        except ValueError:
            # The previous config was valid, no need to validate it again
            self.nycthemeral_cycle.update(previous)
            self.reset_nycthemeral_caches()
            raise
        # self.reset_nycthemeral_caches()  # Done in refresh_lighting_hours()
        await self.refresh_lighting_hours()

    @property
    def nycthemeral_span_target(self) -> str | None:
        return self.nycthemeral_cycle["target"]

    async def set_nycthemeral_span_target(
            self,
            target: str | None,
            refresh: bool = True,
    ) -> None:
        """Set the target location for nycthemeral span calculations.

        :param target: Name of a place defined in private config, or None.
        :param refresh: Whether to refresh the lighting hours.
        :raises ValueError: If the target place is not defined.
        """
        if target is not None:
            place = self.general.get_place(target)
            if place is None:
                raise ValueError(
                    "The place targeted must first be set with "
                    "`EngineConfig.set_place` before using it as a target."
                )
        self.nycthemeral_cycle["target"] = target
        self.reset_nycthemeral_caches()
        if refresh:
            await self.refresh_lighting_hours()

    @staticmethod
    def check_nycthemeral_method(
            method: gv.LightingMethod | gv.NycthemeralSpanMethod,
            ecosystem_dict: EcosystemConfigDict,
            places: Collection[str],
    ) -> None:
        """Validate that a nycthemeral or lighting method can be used.

        :param method: The lighting or nycthemeral span method to validate.
        :param ecosystem_dict: The ecosystem configuration dictionary.
        :param places: Dictionary of available places with coordinates.
        :raises ConfigValidationError: If the method requires a target that is
                                       not available or configured.
        """
        if method in (gv.LightingMethod.fixed, gv.NycthemeralSpanMethod.fixed):
            return  # No target needed
        # Try to get the target
        target: str | None
        if method is gv.LightingMethod.elongate:
            target = DEFAULT_PLACE
        elif method is gv.NycthemeralSpanMethod.mimic:
            nyct_cfg: gv.NycthemeralCycleConfigDict = \
                ecosystem_dict["environment"]["nycthemeral_cycle"]
            target = nyct_cfg.get("target")
            if target is None:
                raise ConfigValidationError(
                    "Nycthemeral span method cannot be 'mimic' as no "
                    "target is specified in the ecosystems configuration file."
                )
        else:  # pragma: no cover
            raise ConfigValidationError(
                "'method' should be either a valid lighting method or a valid "
                "nycthemeral span method."
            )

        m = "Lighting" if isinstance(method, gv.LightingMethod) else "Nycthemeral span"
        # Verify we have the target's coordinates
        if target not in places:
            raise ConfigValidationError(
                f"{m} method cannot be '{method.name}' as the coordinates of "
                f"'{target}' are not provided in the private configuration file."
            )
        # Assume the sun times can be computed

    def _compute_nycthemeral_span_method(self) -> gv.NycthemeralSpanMethod:
        span_method: gv.NycthemeralSpanMethod = safe_enum_from_name(
            gv.NycthemeralSpanMethod, self.nycthemeral_cycle["span"])
        # Log any incompatibilities with the nycthemeral span method chosen
        try:
            self.check_nycthemeral_method(
                span_method, self._config_dict, self.general.private_config["places"])
        except ConfigValidationError as e:
            self.logger.warning(f"{e} Will fall back to 'fixed'.")
        # If using fixed method, no check required
        if span_method == gv.NycthemeralSpanMethod.fixed:
            return gv.NycthemeralSpanMethod.fixed
        # Else, we need to make sure we have suntimes for the nycthemeral target
        target = self.nycthemeral_span_target
        if target is None:
            return gv.NycthemeralSpanMethod.fixed
        sun_times = self.general.get_sun_times(target)
        if sun_times is None:
            return gv.NycthemeralSpanMethod.fixed
        else:
            return gv.NycthemeralSpanMethod.mimic

    @property
    def nycthemeral_span_method(self) -> gv.NycthemeralSpanMethod:
        if self._nycthemeral_span_method is None:
            self._nycthemeral_span_method = self._compute_nycthemeral_span_method()
        return self._nycthemeral_span_method

    async def set_nycthemeral_span_method(
            self,
            method: gv.NycthemeralSpanMethod,
            refresh: bool = True,
    ) -> None:
        """Set the method for determining nycthemeral span (day/night periods).

        :param method: Either 'fixed' or 'mimic' to follow a target location.
        :param refresh: Whether to refresh the lighting hours.
        :raises ConfigValidationError: If the method requires unavailable configuration.
        """
        method = safe_enum_from_name(gv.NycthemeralSpanMethod, method)
        self.check_nycthemeral_method(
            method, self._config_dict, self.general.private_config["places"])
        self.nycthemeral_cycle["span"] = method
        self.reset_nycthemeral_caches()
        if refresh:
            await self.refresh_lighting_hours()

    def _compute_nycthemeral_span_hours(self) -> gv.NycthemeralSpanConfig:
        if self.nycthemeral_span_method == gv.NycthemeralSpanMethod.mimic:
            target = self.nycthemeral_span_target
            # With mimic as a span method, target should not be None
            assert target is not None
            # Mimic the day length of the target, as if it was under the same
            # longitude as `home`
            sun_times = self.general.get_sun_times(target)
            if sun_times is not None:
                assert sun_times["sunrise"] is not None
                assert sun_times["sunset"] is not None
                return gv.NycthemeralSpanConfig(
                    day=sun_times["sunrise"],
                    night=sun_times["sunset"],
                )
        return gv.NycthemeralSpanConfig(
            day=self.nycthemeral_cycle["day"],
            night=self.nycthemeral_cycle["night"],
        )

    @property
    def nycthemeral_span_hours(self) -> gv.NycthemeralSpanConfig:
        if self._nycthemeral_span_hours is None:
            self._nycthemeral_span_hours = self._compute_nycthemeral_span_hours()
        return self._nycthemeral_span_hours

    async def set_nycthemeral_span_hours(
            self,
            value: gv.NycthemeralSpanConfigDict,
            refresh: bool = True,
    ) -> None:
        """Set time parameters

        :param value: A dict in the form {'day': '8h00', 'night': '22h00'}
        :param refresh: Whether to refresh the lighting hours.
        """
        try:
            validated_value = gv.NycthemeralSpanConfig(**value).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid time parameters provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
        self.nycthemeral_cycle.update(validated_value)
        self.reset_nycthemeral_caches()
        if refresh:
            await self.refresh_lighting_hours()

    def is_day(self, now: time | None = None) -> bool:
        nycthemeral_span = self.nycthemeral_span_hours
        return is_time_between(
            nycthemeral_span.day,
            nycthemeral_span.night,
            now or datetime.now().time(),
        )

    def _compute_lighting_method(self) -> gv.LightingMethod:
        lighting_method: gv.LightingMethod = safe_enum_from_name(
            gv.LightingMethod, self.nycthemeral_cycle["lighting"])
        # Log any incompatibilities with the lighting method chosen
        try:
            self.check_nycthemeral_method(
                lighting_method, self._config_dict, self.general.private_config["places"])
        except ConfigValidationError as e:
            self.logger.warning(f"{e} Will fall back to 'fixed'.")
        # If using fixed method, no check is required
        if lighting_method == gv.LightingMethod.fixed:
            return gv.LightingMethod.fixed
        # Otherwise, we need to make sure we have suntimes for "home"
        sun_times = self.general.get_sun_times(DEFAULT_PLACE)
        if sun_times is None:
            return gv.LightingMethod.fixed
        else:
            return gv.LightingMethod.elongate

    @property
    def lighting_method(self) -> gv.LightingMethod:
        if self._lighting_method is None:
            self._lighting_method = self._compute_lighting_method()
        return self._lighting_method

    async def set_lighting_method(
            self,
            method: gv.LightingMethod,
            refresh: bool = True,
    ) -> None:
        method = safe_enum_from_name(gv.LightingMethod, method)
        self.check_nycthemeral_method(
            method, self._config_dict, self.general.private_config["places"])
        self.nycthemeral_cycle["lighting"] = method
        self.reset_nycthemeral_caches()
        if refresh:
            await self.refresh_lighting_hours()

    def _compute_lighting_hours(self) -> gv.LightingHours:
        # Start by getting morning_start and evening_end
        nycthemeral_span: gv.NycthemeralSpanConfig = self.nycthemeral_span_hours
        morning_start: time = nycthemeral_span.day
        evening_end: time = nycthemeral_span.night
        # Then fill in morning_end and evening_start
        morning_end: time
        evening_start: time
        lighting_method: gv.LightingMethod = self.lighting_method

        # Computation for 'fixed' lighting method
        if lighting_method == gv.LightingMethod.fixed:
            dt_morning_start: datetime = to_dt(morning_start)
            day_span: timedelta = to_dt(evening_end) - dt_morning_start
            half_day: datetime = dt_morning_start + (day_span / 2)
            morning_end = (half_day - timedelta(milliseconds=1)).time()
            evening_start = half_day.time()

        # Computation for 'elongate' lighting method
        elif lighting_method == gv.LightingMethod.elongate:
            home_sun_times = self.general.home_sun_times
            # `lighting_method` should not be allowed to be set to `elongate` if
            #  `home_sun_times` is not computable.
            assert home_sun_times is not None
            # `_compute_sun_times` should have computed virtual `sunrise` and
            #  `sunset` times for polar days and nights
            assert home_sun_times["sunrise"] is not None
            assert home_sun_times["sunset"] is not None
            sunrise: datetime = to_dt(home_sun_times["sunrise"])
            sunset: datetime = to_dt(home_sun_times["sunset"])
            # Civil dawn can be None for high latitude at dates close to solstices.
            # In this case, use an offset of 1h30
            civil_dawn_time: time | None = home_sun_times["civil_dawn"]
            offset: timedelta
            if civil_dawn_time is None:
                offset = timedelta(hours=1, minutes=30)
            else:
                civil_dawn: datetime = to_dt(civil_dawn_time)
                offset = sunrise - civil_dawn
            morning_end = (sunrise + offset).time()
            evening_start = (sunset - offset).time()
        else:  # Should not be possible
            raise ValueError

        return gv.LightingHours(
            morning_start=morning_start,
            # Morning should not end later than evening
            morning_end=min(morning_end, evening_end),
            # Evening should not start before morning
            evening_start=max(evening_start, morning_start),
            evening_end=evening_end,
        )

    @property
    def lighting_hours(self) -> gv.LightingHours:
        if self._lighting_hours is None:
            self._lighting_hours = self._compute_lighting_hours()
        return self._lighting_hours

    async def refresh_lighting_hours(self) -> None:
        self.logger.info("Refreshing lighting hours.")

        # Reset caches ...
        self.reset_nycthemeral_caches()

        # ... compute the span method and hours ...
        self._nycthemeral_span_method = self._compute_nycthemeral_span_method()
        self._nycthemeral_span_hours = self._compute_nycthemeral_span_hours()

        # ... and the lighting method and hours
        self._lighting_method = self._compute_lighting_method()
        self._lighting_hours = self._compute_lighting_hours()

    def is_lighting_needed(self, now: time | None = None) -> bool:
        now = now or datetime.now().time()
        hours = self.lighting_hours
        if self.lighting_method == gv.LightingMethod.elongate:
            # If `lighting_method` is `elongate`, `morning_end` and `evening_start`
            #  should have been computed
            assert hours.morning_end is not None
            assert hours.evening_start is not None
            # The aim of `elongate` is to supplement natural lighting before
            # sunrise and after the sunset. It shouldn't cross midnight. Just
            # test if time is between the lightning hours
            if (
                hours.morning_start <= now <= hours.morning_end
                or hours.evening_start <= now <= hours.evening_end
            ):
                return True
            else:
                return False
        else:
            return is_time_between(hours.morning_start, hours.evening_end, now)

    # ---------------------------------------------------------------------------
    #      Chaos parameters
    # ---------------------------------------------------------------------------
    @property
    def chaos_config(self) -> gv.ChaosConfig:
        return gv.ChaosConfig(**self.environment["chaos"])

    def set_chaos_config(self, **value: Unpack[gv.ChaosConfigDict]) -> None:
        """Set chaos parameter

        :param value: The chaos config values with the entries 'frequency': int,
                      'duration': int and 'intensity': float.
        """
        try:
            validated_values = gv.ChaosConfig(**value).model_dump()
        except pydantic.ValidationError as e:  # pragma: no cover
            raise ValueError(
                f"Invalid chaos parameters provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
        self.environment["chaos"] = validated_values

    @property
    def chaos_time_window(self) -> gv.TimeWindowDict:
        chaos_memory = self.general.get_chaos_memory(self.uid)
        if chaos_memory["last_update"] < date.today():
            self.logger.warning("Chaos time window is outdated.")
            raise OutdatedData("Run `update_chaos_time_window()`")
        return self.general.get_chaos_memory(self.uid)["time_window"]

    def _set_chaos_time_window(
            self,
            beginning: datetime | None,
            end: datetime | None,
    ) -> None:
        self._engine_config.chaos_memory[self.uid] = ChaosMemoryValidator(
            time_window={"beginning": beginning, "end": end},
        ).model_dump()

    async def update_chaos_time_window(self) -> None:
        """Update the chaos time window if it hasn't been updated today.

        Randomly determines whether a chaos period should begin based on the
        configured frequency.
        """
        self.logger.info("Updating chaos time window.")
        if self.general.get_chaos_memory(self.uid)["last_update"] >= date.today():
            self.logger.debug("Chaos time window is already up to date.")
            return
        # If no frequency is given, chaos is not wanted
        if not self.chaos_config.frequency:
            self._set_chaos_time_window(None, None)
            await self.general.save(CacheType.chaos)
            return
        chaos_memory = self.general.get_chaos_memory(self.uid)
        beginning = chaos_memory["time_window"]["beginning"]
        end = chaos_memory["time_window"]["end"]
        # If we're in the middle of a scheduled chaos time window
        if (
                beginning and end
                and beginning <= datetime.now(timezone.utc) <= end
        ):
            # Simply update the time window "last_update"
            self._set_chaos_time_window(beginning, end)
            await self.general.save(CacheType.chaos)
            return
        # Otherwise, we are in a peaceful time window ...
        beginning = None
        end = None
        # ... roll the dice to see if a chaos time window is going to be scheduled
        # The probability of a chaos time window = 1/frequency
        chaos_probability = random.randint(1, self.chaos_config.frequency)
        if chaos_probability == 1:
            midnight = datetime.now()
            midnight = midnight.replace(hour=0, minute=0, second=0, microsecond=0)
            beginning = midnight.astimezone(timezone.utc)
            # Make sure the end is before the next CRON update so 2 chaotic days
            #  can follow each others if frequency = 1
            end = midnight + timedelta(days=self.chaos_config.duration, seconds=-1)
            end = end.astimezone(timezone.utc)
        self._set_chaos_time_window(beginning, end)
        await self.general.save(CacheType.chaos)

    def get_chaos_factor(self, now: datetime | None = None) -> float:
        """Calculate the current chaos factor based on time within chaos window.

        Returns a sinusoidal factor that peaks at the middle of the chaos period.

        :param now: The current time, defaults to now in UTC.
        :return: A float from 1.0 (no chaos) to 1 + chaos intensity (peak chaos).
        """
        try:
            time_window = self.chaos_time_window
        except OutdatedData:
            return 1.0
        beginning = time_window["beginning"]
        end = time_window["end"]
        if beginning is None or end is None:
            return 1.0
        now = now or datetime.now(timezone.utc)
        chaos_duration = (end - beginning).total_seconds() // 60
        chaos_start_to_now = (now - beginning).total_seconds() // 60
        chaos_fraction = chaos_start_to_now / chaos_duration
        if not 0.0 <= chaos_fraction <= 1.0:
            return 1.0
        chaos_radian = chaos_fraction * pi
        return (sin(chaos_radian) * self.chaos_config.intensity) + 1.0

    @property
    def chaos_parameters(self) -> gv.ChaosParameters:
        try:
            time_window = self.chaos_time_window
        except OutdatedData:
            time_window = {
                "beginning": None,
                "end": None,
            }
        # Valid ignore: implicit conversion from dict to model by pydantic
        return gv.ChaosParameters(**{  # ty: ignore[invalid-argument-type]
            **self.environment["chaos"],
            "time_window": time_window,
        })

    # ---------------------------------------------------------------------------
    #      Climate parameters
    # ---------------------------------------------------------------------------
    @property
    def climate(self) -> dict[gv.ClimateParameter, gv.AnonymousClimateConfigDict]:
        """
        Returns the climate config for the ecosystem
        """
        try:
            return self.environment["climate"]
        except KeyError:  # pragma: no cover
            self.environment["climate"] = {}
            return self.environment["climate"]

    def has_climate_parameter(self, parameter: gv.ClimateParameter) -> bool:
        """Check if a climate parameter is configured for this ecosystem."""
        parameter = parameter_adapter.validate_python(parameter)
        return parameter in self.climate

    def get_climate_parameter(
            self,
            parameter: gv.ClimateParameter,
    ) -> gv.ClimateConfig:
        """Get the configuration for a specific climate parameter.

        :param parameter: The climate parameter name.
        :return: The climate configuration for the parameter.
        :raises UndefinedParameter: If the parameter is not configured.
        """
        parameter = parameter_adapter.validate_python(parameter)
        try:
            data = self.climate[parameter]
        except KeyError:
            raise UndefinedParameter(
                f"No climate parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file."
                f"{closest_parameter_hint(parameter, self.climate.keys())}"
            )
        else:
            return gv.ClimateConfig(parameter=parameter, **data)

    def set_climate_parameter(
            self,
            parameter: gv.ClimateParameter,
            **value: Unpack[gv.AnonymousClimateConfigDict],
    ) -> None:
        """Set or create a climate parameter configuration.

        If `linked_actuators` is missing or None, the default linked actuators
        of the parameter are used.

        :param parameter: The climate parameter name.
        :param value: The climate configuration values.
        :raises ValueError: If the provided values are invalid, if no sensor
                            of the ecosystem measures the parameter or if one
                            of its actuator groups already has a different climate
                            role.
        """
        parameter = parameter_adapter.validate_python(parameter)
        try:
            validated_value = gv.AnonymousClimateConfig(**value).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid climate parameters provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
        if validated_value["linked_actuators"] is None:
            validated_value["linked_actuators"] = \
                self.get_default_climate_linked_actuator(parameter)
        # Make sure the ecosystem can measure the new climate parameter
        self.check_climate_sensors(parameter, validated_value, self.hardware_dict)
        # Make sure the new climate parameter's actuators have no other
        #  climate role, neither through their group ...
        self.check_climate_actuators(parameter, validated_value, self.climate)
        # ... nor through another group of their hardware. Only the hardware in
        #  the parameter's linked groups can gain a conflict
        new_climate = {**self.climate, parameter: validated_value}
        linked_groups = {
            group for group in validated_value["linked_actuators"].values()
            if group is not None
        }
        errors: list[str] = []
        for hardware_uid, hardware_cfg in self.hardware_dict.items():
            if linked_groups.isdisjoint(hardware_cfg["groups"]):
                continue
            try:
                self.check_hardware_groups(hardware_uid, hardware_cfg, new_climate)
            except ConfigValidationError as e:
                errors.append(str(e))
        if errors:
            raise ConfigValidationError(" ".join(errors))
        self.climate[parameter] = validated_value

    def update_climate_parameter(
            self,
            parameter: gv.ClimateParameter,
            **value: Unpack[gv.AnonymousClimateConfigDict],
    ) -> None:
        """Update an existing climate parameter configuration.

        :param parameter: The climate parameter name.
        :param value: The climate configuration values to update.
        :raises UndefinedParameter: If the parameter does not exist.
        """
        parameter = parameter_adapter.validate_python(parameter)
        if not self.climate.get(parameter):
            raise UndefinedParameter(
                f"No climate parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file."
                f"{closest_parameter_hint(parameter, self.climate.keys())}"
            )
        current_value: gv.AnonymousClimateConfigDict = self.climate[parameter].copy()
        current_value.update(**remove_missing(value))  # ty: ignore[invalid-argument-type]
        self.set_climate_parameter(parameter, **current_value)

    def delete_climate_parameter(
            self,
            parameter: gv.ClimateParameter,
    ) -> None:
        """Delete a climate parameter from the configuration.

        :param parameter: The climate parameter name.
        :raises UndefinedParameter: If the parameter does not exist.
        """
        parameter = parameter_adapter.validate_python(parameter)
        try:
            del self.climate[parameter]
        except KeyError:
            raise UndefinedParameter(
                f"No climate parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file"
                f"{closest_parameter_hint(parameter, self.climate.keys())}"
            )

    @staticmethod
    def _get_sensed_measures(
            hardware: dict[str, gv.AnonymousHardwareConfigDict],
    ) -> set[str]:
        """Get the measures taken by the sensors of an ecosystem.

        A sensor without any measure configured takes all the measures its
        model provides.
        """
        rv: set[str] = set()
        for hardware_cfg in hardware.values():
            hardware_cls = hardware_models.get(hardware_cfg["model"])
            if hardware_cls is None or not issubclass(hardware_cls, SensorMixin):
                continue
            measures = hardware_cfg.get("measures")
            if measures:
                rv.update(measure["name"].lower() for measure in measures)
            elif isinstance(hardware_cls.measures_available, dict):
                rv.update(measure.name for measure in hardware_cls.measures_available)
        return rv

    @classmethod
    def check_climate_sensors(
            cls,
            parameter: gv.ClimateParameter,
            climate_cfg: gv.AnonymousClimateConfigDict,
            hardware: dict[str, gv.AnonymousHardwareConfigDict],
    ) -> None:
        """Check that the ecosystem can "measure" a climate parameter.

        A climate parameter is regulated thanks to the measure it is linked to
        (its own name by default). This method checks that at least one sensor
        can sense this measure.

        `light` is the exception as the `Light` subroutine works without sensor.

        :param parameter: The climate parameter name.
        :param climate_cfg: The validated climate config of the parameter.
        :param hardware: The validated hardware config of the ecosystem.
        :raises ConfigValidationError: If no sensor takes the measure of the
                                       parameter.
        """
        if parameter == "light":
            return
        measure = climate_cfg.get("linked_measure") or parameter
        sensed_measures = cls._get_sensed_measures(hardware)
        if measure in sensed_measures:
            return
        choices = {*default_actuators.DEFAULT_CLIMATE_PARAMETERS, *sensed_measures}
        raise ConfigValidationError(
            f"Climate parameter '{parameter}' needs sensor measure '{measure}' "
            f"but it cannot be found.{closest_parameter_hint(measure, choices)}"
        )

    def get_scaled_climate_target(
            self,
            parameter: gv.ClimateParameter,
    ) -> ScaledClimateTarget:
        parameter = parameter_adapter.validate_python(parameter)
        if parameter == "light" and not self.has_climate_parameter("light"):
            # Unconfigured light runs on/off by default: a large positive day
            # value forces the PID fully on, a negative night value forces it fully off.
            cfg = DEFAULT_LIGHT_CLIMATE_CFG
        else:
            cfg = self.get_climate_parameter(parameter)
        chaos = self.get_chaos_factor()
        return ScaledClimateTarget(cfg.day * chaos, cfg.night * chaos, cfg.hysteresis * chaos)

    # ---------------------------------------------------------------------------
    #      Weather parameters
    # ---------------------------------------------------------------------------
    @property
    def weather(self) -> dict[gv.WeatherParameter, gv.AnonymousWeatherConfigDict]:
        """
        Returns the weather config for the ecosystem
        """
        return self.environment["weather"]

    def has_weather_parameter(self, parameter: gv.WeatherParameter) -> bool:
        """Check if a weather parameter is configured for this ecosystem."""
        parameter = parameter_adapter.validate_python(parameter)
        return parameter in self.weather

    def get_weather_parameter(self, parameter: gv.WeatherParameter) -> gv.WeatherConfig:
        """Get the configuration for a specific weather parameter.

        :param parameter: The weather parameter name.
        :return: The weather configuration for the parameter.
        :raises UndefinedParameter: If the parameter is not configured.
        """
        parameter = parameter_adapter.validate_python(parameter)
        try:
            data = self.weather[parameter]
        except KeyError:
            raise UndefinedParameter(
                f"No weather parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file."
                f"{closest_parameter_hint(parameter, self.weather.keys())}"
            )
        else:
            return gv.WeatherConfig(parameter=parameter, **data)

    def set_weather_parameter(
            self,
            parameter: gv.WeatherParameter,
            **value: Unpack[gv.AnonymousWeatherConfigDict],
    ) -> None:
        """Set or create a weather parameter configuration.

        If `linked_actuator` is missing or None, the default linked actuator
        of the parameter, if any, is used.

        :param parameter: The weather parameter name.
        :param value: The weather configuration values.
        :raises ValueError: If the provided values are invalid.
        """
        parameter = parameter_adapter.validate_python(parameter)
        try:
            validated_value = gv.AnonymousWeatherConfig(**value).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid weather config provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`."
            )
        if validated_value["linked_actuator"] is None:
            validated_value["linked_actuator"] = \
                self.get_default_weather_linked_actuator(parameter)
        self.weather[parameter] = validated_value

    def update_weather_parameter(
            self,
            parameter: gv.WeatherParameter,
            **value: Unpack[gv.AnonymousWeatherConfigDict],
    ) -> None:
        """Update an existing weather parameter configuration.

        :param parameter: The weather parameter name.
        :param value: The weather configuration values to update.
        :raises UndefinedParameter: If the parameter does not exist.
        """
        parameter = parameter_adapter.validate_python(parameter)
        if not self.weather.get(parameter):
            raise UndefinedParameter(
                f"No weather parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file."
                f"{closest_parameter_hint(parameter, self.weather.keys())}"
            )
        current_value: gv.AnonymousWeatherConfigDict = self.weather[parameter].copy()
        current_value.update(**remove_missing(value))  # ty: ignore[invalid-argument-type]
        self.set_weather_parameter(parameter, **current_value)

    def delete_weather_parameter(
            self,
            parameter: gv.WeatherParameter,
    ) -> None:
        """Delete a weather parameter from the configuration.

        :param parameter: The weather parameter name.
        :raises UndefinedParameter: If the parameter does not exist.
        """
        parameter = parameter_adapter.validate_python(parameter)
        try:
            del self.weather[parameter]
        except KeyError:
            raise UndefinedParameter(
                f"No weather parameter {parameter} was found for ecosystem "
                f"'{self.name}' in ecosystems configuration file."
                f"{closest_parameter_hint(parameter, self.weather.keys())}"
            )

    # ---------------------------------------------------------------------------
    #   Actuator groups
    # ---------------------------------------------------------------------------
    def get_valid_actuator_groups(self) -> set[str]:
        """Get the set of valid actuator group names for this ecosystem."""
        # TODO: If "free" actuators are ever added, add them here too
        return {
            *self.get_climate_direction_to_group().values(),
            *self.get_weather_direction_to_group().values(),
        }

    # ---------------------------------------------------------------------------
    #      Climate roles
    # ---------------------------------------------------------------------------
    @staticmethod
    def get_default_climate_linked_actuator(
            parameter: gv.ClimateParameter,
    ) -> gv.ActuatorCoupleDict:
        """Get the default actuator groups linked to a climate parameter.

        :param parameter: The climate parameter name.
        :return: The default actuator group of each direction, None when the
                 parameter has no default actuator group for this direction.
        """
        defaults = default_actuators.climate_to_group_mapping
        return {
            "increase": defaults.get((parameter, "increase")),
            "decrease": defaults.get((parameter, "decrease")),
        }

    @classmethod
    def _compute_climate_direction_to_group(
            cls,
            climate: dict[gv.ClimateParameter, gv.AnonymousClimateConfigDict]
    ) -> dict[ClimateDirection, str]:
        """Get actuator couples for all climate parameters.

        A direction set to None has no actuator group, and a parameter that
        is not configured has none at all.

        `light` is the exception: the `Light` subroutine can work without any
        climate config, so it should always have an actuator group, the default
        one if none other is configured.
        """
        directions: tuple[Direction, Direction] = ("increase", "decrease")
        rv: dict[ClimateDirection, str] = {}
        for parameter, climate_cfg in climate.items():
            linked_actuators = climate_cfg.get("linked_actuators")
            if linked_actuators is None:
                # The climate config has not been normalized (yet)
                linked_actuators = cls.get_default_climate_linked_actuator(parameter)
            for direction in directions:
                actuator_group = linked_actuators[direction]
                if actuator_group is not None:
                    rv[(parameter, direction)] = actuator_group
        # Light can be used without a customized climate config and so needs a
        #  default actuator group
        light_direction: ClimateDirection = ("light", "increase")
        if light_direction not in rv:
            rv[light_direction] = default_actuators.climate_to_group_mapping[light_direction]
        return rv

    def get_climate_direction_to_group(self) -> dict[ClimateDirection, str]:
        """Get actuator couples for all climate parameters.

        The linked actuators of the configured climate parameters, plus the
        actuator group of `light`, which is always in use.
        """
        return self._compute_climate_direction_to_group(self.climate)

    def get_group_to_climate_direction(self, actuator_group: str) -> ClimateDirection | None:
        """Get the climate parameter and direction an actuator group serves.

        An actuator group can serve a climate and weather roles at the same time
        (`fan` for `cooler` and `air_purity`): the climate role is returned,
        as it is the one that needs a direction and a PID.

        :param actuator_group: The name of the actuator group.
        :return: The climate parameter and direction, or None if the group only
                 serves weather parameters or is unknown.
        """
        for climate_direction, group in self.get_climate_direction_to_group().items():
            if group == actuator_group:
                return climate_direction
        return None

    @classmethod
    def _compute_group_to_climate_directions(
            cls,
            climate: dict[gv.ClimateParameter, gv.AnonymousClimateConfigDict],
    ) -> dict[str, list[ClimateDirection]]:
        """Get the climate roles served by each actuator group."""
        rv: dict[str, list[ClimateDirection]] = {}
        climate_to_group = cls._compute_climate_direction_to_group(climate)
        for climate_direction, actuator_group in climate_to_group.items():
            rv.setdefault(actuator_group, []).append(climate_direction)
        return rv

    @staticmethod
    def _format_climate_directions(climate_directions: list[ClimateDirection]) -> str:
        return humanize_list([
            f"'{parameter}' ({direction})"
            for parameter, direction in climate_directions
        ])

    @classmethod
    def check_climate_groups(
            cls,
            climate: dict[gv.ClimateParameter, gv.AnonymousClimateConfigDict],
    ) -> None:
        """Check that each actuator group serves at most one climate role.

        A climate role is a climate parameter and its direction, such as
        `temperature` / `decrease`. Two roles driving the same actuators would
        have two PIDs fight over them, so an actuator group can only be linked
        to a single climate role.
        Weather parameters don't have these limitations: a weather event
        overrides the climate role of its actuator group while it lasts (a few
        minutes in theory).

        Used to validate a whole climate config: each conflict is reported
        once, from the actuator group's point of view. To validate a single
        climate parameter, use `check_climate_actuators()`.

        :param climate: The validated climate config of the ecosystem.
        :raises ConfigValidationError: If an actuator group serves more than
                                       one climate role.
        """
        errors: list[str] = [
            f"Actuator group '{actuator_group}' is linked to "
            f"{cls._format_climate_directions(climate_directions)}."
            for actuator_group, climate_directions
            in cls._compute_group_to_climate_directions(climate).items()
            if len(climate_directions) > 1
        ]
        if errors:
            raise ConfigValidationError(
                f"An actuator group can only serve one climate parameter and "
                f"direction. {' '.join(errors)}"
            )

    @classmethod
    def check_climate_actuators(
            cls,
            parameter: gv.ClimateParameter,
            climate_cfg: gv.AnonymousClimateConfigDict,
            climate: dict[gv.ClimateParameter, gv.AnonymousClimateConfigDict],
    ) -> None:
        """Check that a climate actuators group has no other climate role.

        :param parameter: The climate parameter name.
        :param climate_cfg: The validated climate config of the parameter.
        :param climate: The climate config of the ecosystem. If it already
                        holds `parameter`, `climate_cfg` replaces it.
        :raises ConfigValidationError: If one of the parameter's actuator
                                       groups serves another climate role,
                                       including the other direction of the
                                       parameter.
        """
        # Merge rather than exclude `parameter`: an unconfigured `light` gets
        #  a default group, so it must be replaced, not removed
        climate = {**climate, parameter: climate_cfg}
        climate_to_group = cls._compute_climate_direction_to_group(climate)
        group_to_climate_directions = cls._compute_group_to_climate_directions(climate)
        errors: list[str] = []
        reported_groups: set[str] = set()
        directions: tuple[Direction, Direction] = ("increase", "decrease")
        for direction in directions:
            actuator_group = climate_to_group.get((parameter, direction))
            # Both directions sharing a group is a single conflict
            if actuator_group is None or actuator_group in reported_groups:
                continue
            others: list[ClimateDirection] = [
                climate_direction
                for climate_direction in group_to_climate_directions[actuator_group]
                if climate_direction != (parameter, direction)
            ]
            if others:
                reported_groups.add(actuator_group)
                errors.append(
                    f"'{parameter}' ({direction}) cannot be linked to actuator "
                    f"group '{actuator_group}', which already serves "
                    f"{cls._format_climate_directions(others)}."
                )
        if errors:
            raise ConfigValidationError(
                f"An actuator group can only serve one climate parameter and "
                f"direction. {' '.join(errors)}"
            )

    # ---------------------------------------------------------------------------
    #      Weather roles
    # ---------------------------------------------------------------------------
    @staticmethod
    def get_default_weather_linked_actuator(
            parameter: gv.WeatherParameter,
    ) -> str | None:
        """Get the default actuator group linked to a weather parameter.

        :param parameter: The weather parameter name.
        :return: The default actuator group, None when the parameter has no
                 default actuator group.
        """
        return default_actuators.weather_to_group_mapping.get((parameter, "increase"))

    def get_weather_direction_to_group(self) -> dict[WeatherDirection, str]:
        """Get the actuator group of all weather parameters.

        A weather parameter without linked actuator has no actuator group, and
        a parameter that is not configured has none either.
        """
        rv: dict[WeatherDirection, str] = {}
        for parameter, weather_cfg in self.weather.items():
            actuator_group = weather_cfg.get("linked_actuator")
            if actuator_group is None:
                # The weather config has not been normalized (yet)
                actuator_group = self.get_default_weather_linked_actuator(parameter)
            if actuator_group is not None:
                rv[(parameter, "increase")] = actuator_group
        return rv

    def get_group_to_weather_direction(self, actuator_group: str) -> list[WeatherDirection] | None:
        """Get the climate parameter and direction an actuator group serves.

        An actuator group can serve a climate and weather roles at the same time
        (`fan` for `cooler` and `air_purity`): the climate role is returned,
        as it is the one that needs a direction and a PID.

        :param actuator_group: The name of the actuator group.
        :return: A list of weather parameter and direction, or None if the group
                 only serves weather parameters or is unknown.
        """
        rv: list[WeatherDirection] = []
        for weather_direction, group in self.get_weather_direction_to_group().items():
            if group == actuator_group:
                rv.append(weather_direction)
        if rv:
            return rv
        return None

    # ---------------------------------------------------------------------------
    #   Hardware parameters
    # ---------------------------------------------------------------------------
    @property
    def hardware_dict(self) -> dict[str, gv.AnonymousHardwareConfigDict]:
        """
        Returns the hardware present in the ecosystem
        """
        try:
            return self._config_dict["hardware"]
        except KeyError:  # pragma: no cover
            self._config_dict["hardware"] = {}
            return self._config_dict["hardware"]

    def get_hardware_group_uids(
        self,
        hardware_type: gv.HardwareType,
        level: gv.HardwareLevel | list[gv.HardwareLevel] | None = None,
    ) -> list[str]:
        """Get UIDs of hardware matching the given type and level.

        :param hardware_type: The type of hardware to filter by.
        :param level: The hardware level(s) to filter by, or None for all.
        :return: List of matching hardware UIDs.
        """
        level = level or [lvl for lvl in gv.HardwareLevel]
        if not isinstance(level, list):
            level = [level]
        return [
            uid
            for uid in self.hardware_dict
            if (
                self.hardware_dict[uid]["type"] in hardware_type
                and self.hardware_dict[uid]["level"] in level
            )
        ]

    def _create_new_short_uid(self) -> str:
        used_ids = {*self.hardware_dict.keys(), *self.plants_dict.keys()}
        while True:
            uid = create_uid(uid_length=16)
            if uid not in used_ids:
                return uid

    def _used_addresses(self) -> list[str]:
        return [
            self.hardware_dict[hardware]["address"]
            for hardware in self.hardware_dict
        ]

    @staticmethod
    def validate_hardware_dict(
            hardware_dict: gv.HardwareConfigDict | HardwareConfigDictInput,
    ) -> gv.HardwareConfigDict:
        """Validate a hardware configuration dictionary.

        :param hardware_dict: The hardware configuration to validate.
        :return: A new validated and normalized hardware dict.
        :raises ValueError: If validation fails.
        """
        try:
            hardware_config = gv.HardwareConfig(**hardware_dict)
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid hardware information provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`"
            )
        if hardware_config.model not in hardware_models:
            raise ValueError(
                "This hardware model is not supported. Use "
                "'EcosystemConfig.supported_hardware()' to see supported hardware."
            )
        hardware_cls = Hardware.get_model_subclass(hardware_config.model)
        # Check address
        address = hardware_cls.validate_address(hardware_config.address)
        # Replace default address with the actual address
        validated_config = hardware_config.model_dump()
        validated_config["address"] = str(address)
        multiplexer_model = hardware_config.multiplexer_model
        if (
                multiplexer_model
                and multiplexer_model not in multiplexer_models
        ):
            raise ValueError(
                f"Multiplexer model '{multiplexer_model}' is not supported."
            )
        return validated_config

    @staticmethod
    def check_hardware_address(
            validated_address: str,
            addresses_used: Collection[str],
    ) -> None:
        """Check that a hardware address is not already used.

        :param validated_address: The already validated hardware address.
        :param addresses_used: Collection of validated addresses already in use.
        :raises ConfigValidationError: If the address is already used.
        """
        if validated_address in addresses_used:
            raise ConfigValidationError(f"Address '{validated_address}' already used.")

    @classmethod
    def check_hardware_groups(
            cls,
            uid: str,
            hardware_cfg: gv.AnonymousHardwareConfigDict,
            climate: dict[gv.ClimateParameter, gv.AnonymousClimateConfigDict],
    ) -> None:
        """Check that a hardware belongs to maximum one actuator group with a
        climate role.

        :param uid: The uid of the hardware.
        :param hardware_cfg: The validated config of the hardware.
        :param climate: The validated climate config of the ecosystem.
        :raises ConfigValidationError: If the hardware belongs to more than one
                                       actuator group with a climate role.
        """
        group_to_climate_directions = cls._compute_group_to_climate_directions(climate)
        climate_groups = sorted(
            group for group in hardware_cfg["groups"]
            if group in group_to_climate_directions
        )
        if len(climate_groups) > 1:
            msgs = [
                f"'{group}' "
                f"({cls._format_climate_directions(group_to_climate_directions[group])})"
                for group in climate_groups
            ]
            raise ConfigValidationError(
                f"Hardware '{hardware_cfg['name']}' ({uid}) belongs to more than "
                f"one actuator group with a climate role: {humanize_list(msgs)}."
            )

    def create_new_hardware(
            self,
            *,
            name: str,
            address: str,
            model: str,
            type: str | gv.HardwareType,
            level: str | gv.HardwareLevel,
            groups: list[str] | set[str] | None = None,
            measures: list | None = None,
            plants: list | None = None,
            active: bool = True,
            multiplexer_model: str | None = None,
    ) -> None:
        """
        Create a new hardware
        :param name: str, the name of the hardware to create
        :param address: str: the address of the hardware to create
        :param model: str: the name of the model of the hardware to create
        :param type: str: the type of hardware to create ('sensor', 'light' ...)
        :param level: str: either 'environment' or 'plants'
        :param measures: list: the list of the measures taken
        :param plants: list: the name of the plant linked to the hardware
        :param active: bool: the status of the hardware. True (active/in use) by default.
        :param multiplexer_model: str: the model of the multiplexer used if there is one
        """
        uid = self._create_new_short_uid()
        hardware_dict = HardwareConfigDictInput(
            uid=uid,
            name=name,
            active=active,
            address=address,
            type=type,
            level=level,
            groups=groups,
            model=model,
            measures=measures,
            plants=plants,
            multiplexer_model=multiplexer_model,
        )
        hardware_dict = self.validate_hardware_dict(hardware_dict)
        self.check_hardware_address(hardware_dict["address"], self._used_addresses())
        uid = hardware_dict["uid"]
        anonymous_hardware_dict = gv.to_anonymous(hardware_dict, "uid")
        self.check_hardware_groups(uid, anonymous_hardware_dict, self.climate)
        self.hardware_dict[uid] = anonymous_hardware_dict

    def update_hardware(
            self,
            uid: str,
            **updating_values: Unpack[AnonymousHardwareConfigDictInput],
    ) -> None:
        """Update an existing hardware configuration.

        :param uid: The UID of the hardware to update.
        :param updating_values: The values to update.
        :raises HardwareNotFound: If no hardware with the given UID exists.
        :raises ValueError: If the updated configuration is invalid.
        """
        if uid not in self.hardware_dict:
            raise HardwareNotFound(
                f"No hardware with uid '{uid}' found in the hardware config."
            )
        anonymous_hardware_dict = self.hardware_dict[uid].copy()
        hardware_dict: gv.HardwareConfigDict = \
            gv.to_identified(anonymous_hardware_dict, {"uid": uid})
        # Replace uid with a special uid for validation so it doesn't conflict
        # with existing hardware
        hardware_dict["uid"] = "__validation__"
        hardware_dict.update(**remove_missing(updating_values))  # ty: ignore[invalid-argument-type]
        hardware_dict = self.validate_hardware_dict(hardware_dict)
        # Don't check address if not trying to update it. To do so, do not pass any address
        # against which to check
        used_addresses = self._used_addresses() if "address" in updating_values else []
        self.check_hardware_address(hardware_dict["address"], used_addresses)
        anonymous_hardware_dict = gv.to_anonymous(hardware_dict, "uid")
        self.check_hardware_groups(uid, anonymous_hardware_dict, self.climate)
        self.hardware_dict[uid] = anonymous_hardware_dict

    def delete_hardware(self, uid: str) -> None:
        """
        Delete a hardware from the config
        :param uid: str, the uid of the hardware to delete
        """
        try:
            del self.hardware_dict[uid]
        except KeyError:
            raise HardwareNotFound(
                f"No hardware with uid '{uid}' found in the hardware config."
            )

    def get_hardware_uid(self, name: str) -> str:
        """Get the UID of a hardware by its name.

        :param name: The name of the hardware.
        :return: The UID of the hardware.
        :raises HardwareNotFound: If no hardware with the given name exists.
        """
        for uid, hardware in self.hardware_dict.items():
            if hardware["name"] == name:
                return uid
        raise HardwareNotFound(
            f"No hardware with name '{name}' found in the hardware config."
        )

    def get_hardware_config(self, uid: str) -> gv.HardwareConfig:
        """Get the full configuration for a hardware by its UID.

        :param uid: The UID of the hardware.
        :return: The hardware configuration.
        :raises HardwareNotFound: If no hardware with the given UID exists.
        """
        try:
            hardware_config = self.hardware_dict[uid]
            return gv.HardwareConfig(uid=uid, **hardware_config)
        except KeyError:
            raise HardwareNotFound(
                f"No hardware with uid '{uid}' found in the hardware config."
            )

    @staticmethod
    def supported_hardware() -> list[str]:
        """Return the list of supported hardware model names."""
        return [h for h in hardware_models]

    # ---------------------------------------------------------------------------
    #   Plants parameters
    # ---------------------------------------------------------------------------
    @property
    def plants_dict(self) -> dict[str, gv.AnonymousPlantConfigDict]:
        """
        Returns the plants present in the ecosystem
        """
        try:
            return self._config_dict["plants"]
        except KeyError:  # pragma: no cover
            self._config_dict["plants"] = {}
            return self._config_dict["plants"]

    def create_new_plant(
            self,
            *,
            name: str,
            species: str,
            sowing_date: datetime | None = None,
            hardware: list[str] | None = None,
    ) -> None:
        """
        Create a new plant
        :param name: str, the name of the plant to create
        :param species: str: the species of the plant to create
        :param sowing_date: datetime: the sowing date of the plant to create
        :param hardware: list: the name of the hardware linked to the plant
        """
        uid = self._create_new_short_uid()
        plant_dict = gv.PlantConfigDict(
            uid=uid,
            name=name,
            species=species,
            sowing_date=sowing_date,
            hardware=hardware or [],
        )
        try:
            plant_dict = gv.PlantConfig(**plant_dict).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid plant information provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`"
            )
        self.plants_dict[uid] = gv.to_anonymous(plant_dict, "uid")

    def update_plant(
            self,
            uid: str,
            **updating_values: Unpack[gv.AnonymousPlantConfigDict],
    ) -> None:
        """Update an existing plant configuration.

        :param uid: The UID of the plant to update.
        :param updating_values: The values to update.
        :raises PlantNotFound: If no plant with the given UID exists.
        :raises ValueError: If the updated configuration is invalid.
        """
        if uid not in self.plants_dict:
            raise PlantNotFound(
                f"No plant with uid '{uid}' found in the plant config."
            )
        anonymous_plant_dict = self.plants_dict[uid].copy()
        plant_dict: gv.PlantConfigDict = \
            gv.to_identified(anonymous_plant_dict, {"uid": uid})
        plant_dict.update(**remove_missing(updating_values))  # ty: ignore[invalid-argument-type]
        try:
            plant_dict = gv.PlantConfig(**plant_dict).model_dump()
        except pydantic.ValidationError as e:
            raise ValueError(
                f"Invalid plant information provided. "
                f"ERROR msg(s): `{format_pydantic_error(e)}`"
            )
        self.plants_dict[uid] = gv.to_anonymous(plant_dict, "uid")

    def delete_plant(self, uid: str) -> None:
        """Delete a plant from the configuration.

        :param uid: The UID of the plant to delete.
        :raises PlantNotFound: If no plant with the given UID exists.
        """
        try:
            del self.plants_dict[uid]
        except KeyError:
            raise PlantNotFound(
                f"No plant with uid '{uid}' found in the plant config."
            )

    def get_plant_uid(self, name: str) -> str:
        """Get the UID of a plant by its name.

        :param name: The name of the plant.
        :return: The UID of the plant.
        :raises PlantNotFound: If no plant with the given name exists.
        """
        for uid, plant in self.plants_dict.items():
            if plant["name"] == name:
                return uid
        raise PlantNotFound(
            f"No plant with name '{name}' found in the plant config."
        )

    def get_plant_config(self, uid: str) -> gv.PlantConfig:
        """Get the full configuration for a plant by its UID.

        :param uid: The UID of the plant.
        :return: The plant configuration.
        :raises PlantNotFound: If no plant with the given UID exists.
        """
        try:
            plant_config = self.plants_dict[uid]
            return gv.PlantConfig(uid=uid, **plant_config)
        except KeyError:
            raise PlantNotFound(
                f"No plant with uid '{uid}' found in the plant config."
            )

