import asyncio
from pathlib import Path

import click

from gaia.config._utils import handlers, logging_config
from gaia.config.from_files import ConfigType, EngineConfig


def _patch_logging(verbose: bool = False):
    # Patch logging to make it more compact and send it to stdout
    logging_config["formatters"]["helper_formatter"] = {
        "format": "%(levelname)-7.7s: %(message)s",
    }
    logging_config["handlers"]["stream_handler"]["formatter"] = "helper_formatter"
    logging_config["loggers"]["gaia.hardware_store"] = {"level": "CRITICAL"}
    if verbose:
        logging_config["handlers"]["stream_handler"]["level"] = "DEBUG"
        logging_config["loggers"]["gaia"]["level"] = "DEBUG"
    handlers.append("stream_handler")


async def _validate_configs(verbose: bool = False) -> None:
    _patch_logging(verbose)
    # Initialize engine config
    engine_config =  EngineConfig()
    engine_config.logger.info("Checking configuration files.")

    any_error: bool = False
    # Validate private config
    engine_config.logger.info("Checking private configuration file.")
    private_cfg_path: Path = engine_config.get_file_path(ConfigType.private)
    engine_config.logger.info(f"Private configuration file path set to: {private_cfg_path}")
    if private_cfg_path.exists():
        engine_config.logger.info("Private configuration file found.")
        try:
            await engine_config.load(ConfigType.private)
        except Exception as e:
            any_error = True
            engine_config.logger.error(
                f"The private configuration file was not validated. ERROR msg(s): `{e}`")
        else:
            engine_config.logger.info("The private configuration file was validated successfully.")
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
        try:
            await engine_config.load(ConfigType.ecosystems)
        except Exception as e:
            any_error = True
            engine_config.logger.error(
                f"The private configuration file was not validated. ERROR msg(s): `{e}`")
        else:
            engine_config.logger.info("The ecosystems configuration file was validated successfully.")
    else:
        engine_config.logger.warning(
            "No ecosystems configuration file found. A default file will be "
            "automatically created.")

    if any_error:
        engine_config.logger.error("One or more configuration files were not validated.")
    else:
        engine_config.logger.info("All configuration files were validated successfully.")


@click.command()
@click.option(
    "--verbose", "-v",
    type=bool,
    default=False,
    help="Enable verbose logging.",
    is_flag=True,
)
def validate_configs(verbose: bool = False) -> None:
    """Validate the ecosystems and the private configuration files."""
    asyncio.run(_validate_configs(verbose))


async def _generate_default_configs(ecosystem: bool = True, private: bool = True) -> None:
    if not any((ecosystem, private)):
        raise ValueError("At least one of the ecosystems or private options must be True.")
    engine_config = EngineConfig()
    engine_config.logger.info("Creating default configuration files.")
    if private:
        private_cfg_path: Path = engine_config.get_file_path(ConfigType.private)
        engine_config.logger.info(f"Private configuration file path set to: {private_cfg_path}")
        if not private_cfg_path.exists():
            engine_config.logger.info("Private configuration file not found. Creating it.")
            async with engine_config.config_files_lock():
                await engine_config._create_private_config_file()
        else:
            engine_config.logger.info("Private configuration file already exists.")
    if ecosystem:
        ecosystems_cfg_path: Path = engine_config.get_file_path(ConfigType.ecosystems)
        engine_config.logger.info(f"Ecosystems configuration file path set to: {ecosystems_cfg_path}")
        if not ecosystems_cfg_path.exists():
            engine_config.logger.info("Ecosystems configuration file not found. Creating it.")
            async with engine_config.config_files_lock():
                await engine_config._create_ecosystems_config_file()
        else:
            engine_config.logger.info("Ecosystems configuration file already exists.")
    engine_config.logger.info("Default configuration file()s created successfully.")


@click.command()
@click.option(
    "--ecosystem/--no-ecosystem",
    type=bool,
    default=True,
    is_flag=True,
)
@click.option(
    "--private/--no-private",
    type=bool,
    default=True,
    is_flag=True,
)
def generate_default_configs(ecosystem: bool = True, private: bool = True) -> None:
    """Create default configuration files."""
    asyncio.run(_generate_default_configs(ecosystem, private))
