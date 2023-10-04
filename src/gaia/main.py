import click


@click.command()
@click.option(
    "--use-green-threads",
    type=bool,
    default=False,
    help="Monkey patch Gaia with eventlet to use green threads",
    show_default=True,
)
def main(
        use_green_threads: bool,
) -> None:
    """Launch Gaia
    """
    if use_green_threads:
        import eventlet

        eventlet.monkey_patch()

    from setproctitle import setproctitle

    setproctitle("gaia")

    from gaia import Engine

    gaia_engine = Engine()
    gaia_engine.init_plugins()
    gaia_engine.run()


if __name__ == "__main__":
    main()
