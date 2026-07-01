from contextlib import asynccontextmanager
from typing import AsyncGenerator, TypeVar

import pytest_asyncio

from gaia.ecosystem import Ecosystem
from gaia.subroutines import (
    Climate, Health, Light, Pictures, Sensors, SubroutineTemplate, Weather)

from tests.subroutines.dummy_subroutine import Dummy


T = TypeVar("T", bound=SubroutineTemplate)

YieldFixture = AsyncGenerator[T, None]


@asynccontextmanager
async def auto_clean(subroutine: T):
    try:
        yield subroutine
    finally:
        if subroutine.started:
            await subroutine.stop()
        if subroutine.enabled:
            subroutine.disable()


# The sensors subroutine is required by others subroutines
@pytest_asyncio.fixture(scope="function")
async def sensors_subroutine(ecosystem: Ecosystem) -> YieldFixture[Sensors]:
    sensor_subroutine: Sensors = ecosystem.get_subroutine("sensors")

    async with auto_clean(sensor_subroutine) as subroutine:
        yield subroutine


# Subroutines needing sensors subroutine
@pytest_asyncio.fixture(scope="function")
async def light_subroutine(
        ecosystem: Ecosystem,
        sensors_subroutine: Sensors,
) -> YieldFixture[Light]:
    # Sensors subroutine is required for full routine
    sensors_subroutine.enable()
    await sensors_subroutine.start()

    light_subroutine: Light = ecosystem.get_subroutine("light")

    async with auto_clean(light_subroutine) as subroutine:
        yield subroutine


@pytest_asyncio.fixture(scope="function")
async def climate_subroutine(
        ecosystem: Ecosystem,
        sensors_subroutine: Sensors,
) -> YieldFixture[Climate]:
    # Sensors subroutine is required
    sensors_subroutine.enable()
    await sensors_subroutine.start()

    climate_subroutine: Climate = ecosystem.get_subroutine("climate")

    async with auto_clean(climate_subroutine) as subroutine:
        yield subroutine


@pytest_asyncio.fixture(scope="function")
async def weather_subroutine(ecosystem: Ecosystem) -> YieldFixture[Weather]:
    weather_subroutine: Weather = ecosystem.get_subroutine("weather")

    async with auto_clean(weather_subroutine) as subroutine:
        yield subroutine


# Subroutines needing camera support
@pytest_asyncio.fixture(scope="function")
async def health_subroutine(ecosystem: Ecosystem) -> YieldFixture[Health]:
    patch_needed = ecosystem.config.get_management("camera") is False
    if patch_needed:
        ecosystem.config.set_management("camera", True)

    health_subroutine: Health = ecosystem.get_subroutine("health")

    async with auto_clean(health_subroutine) as subroutine:
        yield subroutine

    if patch_needed:
        ecosystem.config.set_management("camera", False)


@pytest_asyncio.fixture(scope="function")
async def pictures_subroutine(ecosystem: Ecosystem) -> YieldFixture[Pictures]:
    patch_needed = ecosystem.config.get_management("camera") is False
    if patch_needed:
        ecosystem.config.set_management("camera", True)

    pictures_subroutine: Pictures = ecosystem.get_subroutine("pictures")

    async with auto_clean(pictures_subroutine) as subroutine:
        yield subroutine

    if patch_needed:
        ecosystem.config.set_management("camera", False)


# Dummy subroutine
@pytest_asyncio.fixture(scope="function")
async def dummy_subroutine(ecosystem: Ecosystem, monkeypatch) -> YieldFixture[Dummy]:
    # Inject the dummy subroutine inside the ecosystem
    monkeypatch.setitem(ecosystem._subroutines, "dummy", Dummy(ecosystem))

    dummy_subroutine: Dummy = ecosystem.get_subroutine("dummy")

    async with auto_clean(dummy_subroutine) as subroutine:
        yield subroutine
