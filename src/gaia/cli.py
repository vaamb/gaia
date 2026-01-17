import os

import click
import uvloop

from gaia import Engine
from gaia.helpers import generate_default_configs, validate_configs


async def main():
    """Launch Gaia"""
    from setproctitle import setproctitle

    setproctitle("gaia")

    gaia_engine = await Engine.new()
    await gaia_engine.run()


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Welcome to GAIA, the Greenhouse Automation Intuitive App"""
    # Don't go further if a subcommand was called
    if ctx.invoked_subcommand is not None:
        return
    # Set libcamera logging level to "WARN" to avoid spurious warnings
    os.environ["LIBCAMERA_LOG_LEVELS"] = "2"

    # Patch anyio's WorkerThread to increase its max idle time
    from anyio._backends._asyncio import WorkerThread

    WorkerThread.MAX_IDLE_TIME = 60

    uvloop.run(main())


cli.add_command(generate_default_configs)
cli.add_command(validate_configs)
