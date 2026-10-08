import asyncio
import sys

from uvicorn import run


if sys.platform == "win32":
    asyncio.set_event_loop_policy(
        asyncio.WindowsProactorEventLoopPolicy()
    )


if __name__ == "__main__":
    run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        # Playwright launches a real Chrome subprocess for LinkedIn/Indeed.
        # Uvicorn's Windows reload worker switches to an event loop that does
        # not implement subprocess transports, so keep this launcher direct.
        reload=False,
        log_level="debug",
    )
