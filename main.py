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
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(shutdown(app)))

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

    await app.start()

    # Keep running until cancelled
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        if wakeword_task:
            wakeword_task.cancel()
        await app.stop()


_shutting_down = False

async def shutdown(app: Application):
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    log = logging.getLogger("main")
    log.info("shutting down...")
    await app.stop()
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.get_event_loop().stop()


if __name__ == "__main__":
    asyncio.run(main())
