import asyncio
from multiprocessing import Process
from time import sleep

from gaia.cli import main


def test_main():
    def wrapper():
        #raise RuntimeError
        asyncio.run(main())

    process = Process(target=wrapper)
    process.start()
    sleep(1)  # Allow to initialize and do a bit of work
    process.terminate()
    process.join(5)  # Should be more than plenty to perform a clean exit
    assert not process.exitcode
