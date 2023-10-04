import click


@click.command()
@click.option(
    "--use-green-threads", "-gt",
    type=bool,
    is_flag=True,
    default=False,
    help="Monkey patch Gaia with gevent to use green threads",
    show_default=True,
)
def main(
        use_green_threads: bool,
) -> None:
    """Launch Gaia
    """
    if use_green_threads:
        from gevent.monkey import patch_all

        patch_all()

    from setproctitle import setproctitle

    setproctitle("gaia")

    from gaia import Engine

    gaia_engine = Engine()
    gaia_engine.init_plugins()
    gaia_engine.run()


if __name__ == "__main__":
    main()
