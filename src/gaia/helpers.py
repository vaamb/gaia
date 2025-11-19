import asyncio
from pathlib import Path

import click

from gaia.config._utils import handlers, logging_config
from gaia.config.from_files import ConfigType, EngineConfig


async def _validate_configs() -> None:
    # Make sure logging will be sent to stdout
    handlers.append("stream_handler")
    logging_config["loggers"]["gaia"]["handlers"] = handlers

    # Initialize engine config
    engine_config =  EngineConfig()
    engine_config.logger.info("Checking configuration files.")

    # Validate private config
    engine_config.logger.info("Checking private configuration file.")
    private_cfg_path: Path = engine_config.get_file_path(ConfigType.private)
    engine_config.logger.info(f"Private configuration file path set to: {private_cfg_path}")
    if private_cfg_path.exists():
        engine_config.logger.info("Private configuration file found.")
        await engine_config.load(ConfigType.private)
    else:
        engine_config.logger.warning(
            "No private configuration file found. A default file will be "
            "automatically created.")

    # Validate ecosystems config
    engine_config.logger.info("Checking ecosystems configuration file.")
    ecosystems_cfg_path: Path = engine_config.get_file_path(ConfigType.ecosystems)
    engine_config.logger.info(f"Ecosystems configuration file path set to: {ecosystems_cfg_path}")
    if ecosystems_cfg_path.exists():
        engine_config.logger.info("Ecosystems configuration file found.")
        await engine_config.load(ConfigType.ecosystems)
    else:
        engine_config.logger.warning(
            "No ecosystems configuration file found. A default file will be "
            "automatically created.")


@click.command()
def validate_configs() -> None:
    """Validate the ecosystems and the private configuration files."""
    asyncio.run(_validate_configs())
