import asyncio

import uvloop

from gaia import main

uvloop.install()
asyncio.run(main())
