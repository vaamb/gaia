from setproctitle import setproctitle


async def main():
    setproctitle("gaia")

    from gaia import Engine

    gaia_engine = Engine()
    await gaia_engine.init_plugins()
    await gaia_engine.run()
