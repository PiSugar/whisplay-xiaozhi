#!/usr/bin/env python3
"""
Test direct WebSocket with device-id in query params instead of headers.
Theory: reverse proxy may be stripping custom headers.
"""

import asyncio
import json
import logging
import ssl
import time

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("diag")

async def main():
    import websockets

    creds_file = __import__("pathlib").Path(__file__).parent / "data" / "credentials.json"
    creds = json.loads(creds_file.read_text())

    mqtt_cid = creds.get("mqtt_client_id", "")
    parts = mqtt_cid.split("@@@")
    mac_colon = parts[1].replace("_", ":") if len(parts) >= 2 else creds.get("device_id", "")
    device_uuid = parts[2] if len(parts) >= 3 else creds.get("client_id", "")

    tests = [
        # Test A: device-id in query params
        {
            "name": "A: device-id in query params",
            "url": f"wss://api.tenclass.net/xiaozhi/v1/?device-id={mac_colon}&client-id={device_uuid}&authorization=Bearer%20test-token",
            "headers": {"protocol-version": "2"},
        },
        # Test B: device-id in both headers AND query
        {
            "name": "B: device-id in headers + query",
            "url": f"wss://api.tenclass.net/xiaozhi/v1/?device-id={mac_colon}",
            "headers": {
                "device-id": mac_colon,
                "client-id": device_uuid,
                "protocol-version": "2",
                "authorization": "Bearer test-token",
            },
        },
        # Test C: standard headers (control)
        {
            "name": "C: standard headers only (control)",
            "url": "wss://api.tenclass.net/xiaozhi/v1/",
            "headers": {
                "device-id": mac_colon,
                "client-id": device_uuid,
                "protocol-version": "2",
                "authorization": "Bearer test-token",
            },
        },
        # Test D: no device-id at all (expect "端口正常" message)
        {
            "name": "D: no device-id (expect server message)",
            "url": "wss://api.tenclass.net/xiaozhi/v1/",
            "headers": {"protocol-version": "2"},
        },
    ]

    hello = json.dumps({
        "type": "hello",
        "version": 2,
        "transport": "websocket",
        "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60},
        "features": {"mcp": True},
    })

    for test in tests:
        log.info("")
        log.info("="*60)
        log.info("TEST: %s", test["name"])
        log.info("  URL: %s", test["url"])
        log.info("  Headers: %s", json.dumps(test["headers"]))
        log.info("="*60)

        t0 = time.monotonic()
        try:
            ssl_ctx = ssl.create_default_context()
            async with websockets.connect(
                test["url"],
                additional_headers=test["headers"],
                ssl=ssl_ctx,
                open_timeout=10,
                close_timeout=5,
            ) as ws:
                elapsed = (time.monotonic() - t0) * 1000
                log.info("[+%.0fms] WebSocket OPEN", elapsed)

                # Try to send hello immediately
                try:
                    await ws.send(hello)
                    elapsed = (time.monotonic() - t0) * 1000
                    log.info("[+%.0fms] Sent hello", elapsed)
                except Exception as e:
                    elapsed = (time.monotonic() - t0) * 1000
                    log.info("[+%.0fms] Failed to send hello: %s", elapsed, e)

                # Receive messages (up to 5 seconds)
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        elapsed = (time.monotonic() - t0) * 1000
                        if isinstance(msg, bytes):
                            log.info("[+%.0fms] RECV binary (%d bytes)", elapsed, len(msg))
                        else:
                            log.info("[+%.0fms] RECV text: %s", elapsed, msg[:500])
                except asyncio.TimeoutError:
                    elapsed = (time.monotonic() - t0) * 1000
                    log.info("[+%.0fms] recv timeout (no more messages)", elapsed)

        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            log.info("[+%.0fms] %s: %s", elapsed, type(e).__name__, e)

        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
