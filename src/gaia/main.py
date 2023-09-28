from setproctitle import setproctitle


def main():
    setproctitle("gaia")

    from gaia import Engine

    gaia_engine = Engine()
    gaia_engine.init_plugins()
    gaia_engine.run()
