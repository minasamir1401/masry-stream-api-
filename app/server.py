import asyncio
import sys

import uvicorn


if __name__ == "__main__":
    # The Windows selector loop keeps the listener alive when video clients
    # disconnect during range requests.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    server = uvicorn.Server(
        uvicorn.Config(
            "app.main:app",
            host="127.0.0.1",
            port=8000,
            access_log=False,
            timeout_keep_alive=30,
        )
    )
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(server.serve())
