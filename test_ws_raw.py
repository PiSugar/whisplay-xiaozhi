#!/usr/bin/env python3
"""
Test WebSocket with different settings:
- No compression
- Raw socket WebSocket (no library overhead)
- Different User-Agent
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
    from websockets.extensions import permessage_deflate

    creds_file = __import__("pathlib").Path(__file__).parent / "data" / "credentials.json"
    creds = json.loads(creds_file.read_text())

    mqtt_cid = creds.get("mqtt_client_id", "")
    parts = mqtt_cid.split("@@@")
    mac_colon = parts[1].replace("_", ":") if len(parts) >= 2 else creds.get("device_id", "")
    device_uuid = parts[2] if len(parts) >= 3 else creds.get("client_id", "")

    headers = {
        "device-id": mac_colon,
        "client-id": device_uuid,
        "protocol-version": "2",
        "authorization": "Bearer test-token",
    }

    hello = json.dumps({
        "type": "hello",
        "version": 2,
        "transport": "websocket",
        "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60},
        "features": {"mcp": True},
    })

    ssl_ctx = ssl.create_default_context()

    # Test 1: No compression extensions
    log.info("="*60)
    log.info("TEST 1: No compression (extensions=[])")
    log.info("="*60)
    t0 = time.monotonic()
    try:
        async with websockets.connect(
            "wss://api.tenclass.net/xiaozhi/v1/",
            additional_headers=headers,
            ssl=ssl_ctx,
            extensions=[],  # Disable permessage-deflate
            compression=None,
            open_timeout=10,
        ) as ws:
            log.info("[+%.0fms] OPEN", (time.monotonic()-t0)*1000)
            await ws.send(hello)
            log.info("[+%.0fms] Sent hello", (time.monotonic()-t0)*1000)
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    log.info("[+%.0fms] RECV: %s", (time.monotonic()-t0)*1000, 
                             msg[:500] if isinstance(msg, str) else f"binary({len(msg)})")
            except asyncio.TimeoutError:
                log.info("[+%.0fms] timeout", (time.monotonic()-t0)*1000)
    except Exception as e:
        log.info("[+%.0fms] %s: %s", (time.monotonic()-t0)*1000, type(e).__name__, e)

    await asyncio.sleep(1)

    # Test 2: ESP32-like User-Agent
    log.info("")
    log.info("="*60)
    log.info("TEST 2: ESP32-like User-Agent")
    log.info("="*60)
    esp_headers = {
        "device-id": mac_colon,
        "client-id": device_uuid,
        "protocol-version": "2",
        "authorization": "Bearer test-token",
        "User-Agent": "IDF/5.3",
    }
    t0 = time.monotonic()
    try:
        async with websockets.connect(
            "wss://api.tenclass.net/xiaozhi/v1/",
            additional_headers=esp_headers,
            ssl=ssl_ctx,
            compression=None,
            open_timeout=10,
            user_agent_header=None,  # Don't override our custom UA
        ) as ws:
            log.info("[+%.0fms] OPEN", (time.monotonic()-t0)*1000)
            await ws.send(hello)
            log.info("[+%.0fms] Sent hello", (time.monotonic()-t0)*1000)
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    log.info("[+%.0fms] RECV: %s", (time.monotonic()-t0)*1000,
                             msg[:500] if isinstance(msg, str) else f"binary({len(msg)})")
            except asyncio.TimeoutError:
                log.info("[+%.0fms] timeout", (time.monotonic()-t0)*1000)
    except Exception as e:
        log.info("[+%.0fms] %s: %s", (time.monotonic()-t0)*1000, type(e).__name__, e)

    await asyncio.sleep(1)

    # Test 3: Raw low-level WebSocket using stdlib (no websockets library)
    log.info("")
    log.info("="*60)
    log.info("TEST 3: Raw HTTP upgrade (stdlib ssl + socket)")
    log.info("="*60)
    t0 = time.monotonic()
    try:
        import socket
        import hashlib
        import base64
        import os

        sock = socket.create_connection(("api.tenclass.net", 443), timeout=10)
        ssl_sock = ssl_ctx.wrap_socket(sock, server_hostname="api.tenclass.net")
        log.info("[+%.0fms] TLS connected", (time.monotonic()-t0)*1000)

        ws_key = base64.b64encode(os.urandom(16)).decode()
        request = (
            "GET /xiaozhi/v1/ HTTP/1.1\r\n"
            "Host: api.tenclass.net\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"device-id: {mac_colon}\r\n"
            f"client-id: {device_uuid}\r\n"
            "protocol-version: 2\r\n"
            "authorization: Bearer test-token\r\n"
            "\r\n"
        )
        ssl_sock.sendall(request.encode())
        log.info("[+%.0fms] Sent HTTP upgrade request", (time.monotonic()-t0)*1000)

        # Read response
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = ssl_sock.recv(4096)
            if not chunk:
                break
            response += chunk
        
        header_text = response.split(b"\r\n\r\n")[0].decode()
        log.info("[+%.0fms] Response:\n%s", (time.monotonic()-t0)*1000, header_text)
        
        remaining = response[response.index(b"\r\n\r\n") + 4:]
        if remaining:
            log.info("[+%.0fms] Extra data after headers: %s", (time.monotonic()-t0)*1000, remaining.hex())

        if "101" in header_text:
            log.info("[+%.0fms] WebSocket upgraded! Sending hello frame...", (time.monotonic()-t0)*1000)
            
            # Build WebSocket text frame (masked, as per RFC 6455 client requirement)
            hello_bytes = hello.encode()
            mask = os.urandom(4)
            
            frame = bytearray()
            frame.append(0x81)  # FIN + TEXT opcode
            length = len(hello_bytes)
            if length < 126:
                frame.append(0x80 | length)  # MASK bit set
            elif length < 65536:
                frame.append(0x80 | 126)
                frame.extend(length.to_bytes(2, 'big'))
            
            frame.extend(mask)
            for i, b in enumerate(hello_bytes):
                frame.append(b ^ mask[i % 4])
            
            ssl_sock.sendall(bytes(frame))
            log.info("[+%.0fms] Sent hello WebSocket frame (%d bytes)", (time.monotonic()-t0)*1000, len(frame))

            # Read responses
            ssl_sock.settimeout(5.0)
            try:
                while True:
                    data = ssl_sock.recv(4096)
                    if not data:
                        log.info("[+%.0fms] Connection closed (EOF)", (time.monotonic()-t0)*1000)
                        break
                    log.info("[+%.0fms] Received %d bytes: %s", (time.monotonic()-t0)*1000, len(data), data[:200].hex())
                    # Try to decode as WebSocket frame
                    if len(data) >= 2:
                        opcode = data[0] & 0x0f
                        fin = (data[0] >> 7) & 1
                        masked = (data[1] >> 7) & 1
                        payload_len = data[1] & 0x7f
                        log.info("  Frame: fin=%d opcode=%d masked=%d payload_len=%d", fin, opcode, masked, payload_len)
                        if opcode == 0x8:  # Close frame
                            if payload_len >= 2:
                                close_code = int.from_bytes(data[2:4], 'big')
                                close_reason = data[4:4+payload_len-2].decode('utf-8', errors='replace')
                                log.info("  CLOSE code=%d reason='%s'", close_code, close_reason)
                            else:
                                log.info("  CLOSE (no code)")
                        elif opcode == 0x1:  # Text frame
                            offset = 2
                            if payload_len == 126:
                                payload_len = int.from_bytes(data[2:4], 'big')
                                offset = 4
                            text = data[offset:offset+payload_len].decode('utf-8', errors='replace')
                            log.info("  TEXT: %s", text[:500])
            except socket.timeout:
                log.info("[+%.0fms] Socket timeout (no more data)", (time.monotonic()-t0)*1000)

        ssl_sock.close()
    except Exception as e:
        log.info("[+%.0fms] %s: %s", (time.monotonic()-t0)*1000, type(e).__name__, e)

if __name__ == "__main__":
    asyncio.run(main())
