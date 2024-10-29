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
    uvloop.run(_main())


if __name__ == "__main__":
    main()
