#!/usr/bin/env python3
"""
whisplay-xiaozhi — XiaoZhi AI voice client for Raspberry Pi + Whisplay HAT.

Entry point: sets up logging, creates Application, and runs the async event loop.
"""

import asyncio
import logging
import signal
import sys

from application import Application
import config


def setup_logging():
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    level = logging.DEBUG if "--debug" in sys.argv else logging.INFO
    logging.basicConfig(level=level, format=fmt)
    # Quiet noisy libraries
    logging.getLogger("websockets").setLevel(logging.WARNING)


async def main():
    setup_logging()
    log = logging.getLogger("main")

    app = Application()

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    shutting_down = False

    def request_shutdown():
        nonlocal shutting_down
        if not shutting_down:
            shutting_down = True
            log.info("shutting down...")
            main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_shutdown)

    # Optionally start wake word detector
    wakeword_task = None
    if config.WAKE_WORD_ENABLED:
        try:
            from wakeword.detector import WakeWordDetector
            detector = WakeWordDetector(on_wake=app.trigger_wake_word)
            wakeword_task = asyncio.create_task(detector.run())
            log.info("wake word detection enabled")
        except Exception as e:
            log.warning("wake word init failed: %s", e)

    try:
        await app.start()
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        if wakeword_task:
            wakeword_task.cancel()
            await asyncio.gather(wakeword_task, return_exceptions=True)
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
