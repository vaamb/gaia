import os

import click
import uvloop


async def _main():
    """Launch Gaia"""
    from setproctitle import setproctitle

    setproctitle("gaia")

    from gaia import Engine

    gaia_engine = Engine()
    if gaia_engine.plugins_needed:
        await gaia_engine.init_plugins()
    await gaia_engine.run()


@click.command()
def main() -> None:
    # Set libcamera logging level to "WARN" to avoid spurious warnings
    os.environ["LIBCAMERA_LOG_LEVELS"] = "2"

    # Patch anyio's WorkerThread to increase its max idle time
    from anyio._backends._asyncio import WorkerThread

    WorkerThread.MAX_IDLE_TIME = 60

    uvloop.run(_main())


if __name__ == "__main__":
    main()
