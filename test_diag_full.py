#!/usr/bin/env python3
"""
Full diagnostic: test both direct WebSocket (mimicking MQTT gateway)
and MQTT connection to identify where the goodbye/close comes from.

Test 1: Direct WebSocket to backend (like the gateway does)
  - Connect to wss://api.tenclass.net/xiaozhi/v1/ with device-id headers
  - Send hello version 2 (what the gateway forwards)
  - Log ALL frames (text, binary, close code/reason)

Test 2: MQTT connection (what we normally do)
  - Connect, subscribe, send hello
  - Log all messages with timestamps

Usage: python3 test_diag_full.py
"""

import asyncio
import json
import logging
import ssl
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("diag")

# Load saved credentials
CRED_FILE = Path(__file__).resolve().parent / "data" / "credentials.json"

def load_creds():
    if CRED_FILE.exists():
        return json.loads(CRED_FILE.read_text())
    log.error("No credentials file: %s", CRED_FILE)
    sys.exit(1)


# ===================== Test 1: Direct WebSocket =====================
async def test_direct_websocket(creds):
    """Connect directly to backend like the MQTT gateway does."""
    try:
        import websockets
    except ImportError:
        log.warning("websockets not installed, skipping direct WS test. pip install websockets")
        return

    ws_url = creds.get("ws_url", "wss://api.tenclass.net/xiaozhi/v1/")

    # Extract MAC from mqtt_client_id: GID_test@@@MAC@@@UUID
    mqtt_cid = creds.get("mqtt_client_id", "")
    parts = mqtt_cid.split("@@@")
    if len(parts) >= 3:
        mac_raw = parts[1]  # e.g. "2C_CF_67_D8_C4_DF"
        mac_colon = mac_raw.replace("_", ":")
        device_uuid = parts[2]
    else:
        mac_colon = creds.get("device_id", "unknown")
        device_uuid = creds.get("client_id", "unknown")

    log.info("="*60)
    log.info("TEST 1: Direct WebSocket to backend")
    log.info("  URL: %s", ws_url)
    log.info("  device-id: %s", mac_colon)
    log.info("  client-id: %s", device_uuid)
    log.info("="*60)

    # Test 1a: With "test-token" (what OTA returns)
    await _ws_test(ws_url, mac_colon, device_uuid, "test-token", "1a: test-token auth")

    # Test 1b: Without authorization header (see if auth is optional)
    await _ws_test(ws_url, mac_colon, device_uuid, None, "1b: no auth header")

    # Test 1c: With ?from=mqtt_gateway query param (what docs say gateway should use)
    url_mqtt = ws_url.rstrip("/") + "?from=mqtt_gateway"
    await _ws_test(url_mqtt, mac_colon, device_uuid, "test-token", "1c: ?from=mqtt_gateway")


async def _ws_test(url, device_id, client_id, auth_token, test_name):
    """Individual WebSocket test."""
    import websockets

    log.info("")
    log.info("--- %s ---", test_name)

    headers = {
        "device-id": device_id,
        "client-id": client_id,
        "protocol-version": "2",
    }
    if auth_token:
        headers["authorization"] = f"Bearer {auth_token}"

    log.info("Headers: %s", json.dumps(headers))

    hello = json.dumps({
        "type": "hello",
        "version": 2,
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": 16000,
            "channels": 1,
            "frame_duration": 60,
        },
        "features": {"mcp": True},
    })

    t0 = time.monotonic()
    try:
        ssl_ctx = ssl.create_default_context()
        async with websockets.connect(
            url,
            additional_headers=headers,
            ssl=ssl_ctx if url.startswith("wss") else None,
            open_timeout=10,
            close_timeout=5,
        ) as ws:
            elapsed = (time.monotonic() - t0) * 1000
            log.info("[+%.0fms] WebSocket OPEN", elapsed)

            # Send hello
            await ws.send(hello)
            elapsed = (time.monotonic() - t0) * 1000
            log.info("[+%.0fms] Sent hello", elapsed)

            # Receive messages until close (timeout 10s)
            try:
                async for msg in asyncio.timeout_at(asyncio.get_event_loop().time() + 10, ws):
                    elapsed = (time.monotonic() - t0) * 1000
                    if isinstance(msg, bytes):
                        log.info("[+%.0fms] RECV binary (%d bytes)", elapsed, len(msg))
                    else:
                        log.info("[+%.0fms] RECV text: %s", elapsed, msg[:500])
                        try:
                            data = json.loads(msg)
                            if data.get("type") == "hello":
                                log.info("  >>> GOT HELLO RESPONSE! session_id=%s", data.get("session_id"))
                            elif data.get("type") == "goodbye":
                                log.info("  >>> GOT GOODBYE")
                            elif data.get("type") == "mcp":
                                log.info("  >>> GOT MCP: method=%s", data.get("payload", {}).get("method"))
                        except json.JSONDecodeError:
                            log.info("  >>> NON-JSON text (maybe auth error?)")
            except TimeoutError:
                elapsed = (time.monotonic() - t0) * 1000
                log.info("[+%.0fms] Timeout (no more messages)", elapsed)

    except websockets.exceptions.InvalidStatusCode as e:
        elapsed = (time.monotonic() - t0) * 1000
        log.info("[+%.0fms] HTTP error: %s", elapsed, e)
    except websockets.exceptions.ConnectionClosedError as e:
        elapsed = (time.monotonic() - t0) * 1000
        log.info("[+%.0fms] Connection closed: code=%s reason='%s'", elapsed, e.code, e.reason)
    except websockets.exceptions.ConnectionClosedOK as e:
        elapsed = (time.monotonic() - t0) * 1000
        log.info("[+%.0fms] Connection closed OK: code=%s reason='%s'", elapsed, e.code, e.reason)
    except Exception as e:
        elapsed = (time.monotonic() - t0) * 1000
        log.info("[+%.0fms] Error: %s: %s", elapsed, type(e).__name__, e)


# ===================== Test 2: MQTT connection =====================
async def test_mqtt(creds):
    """Standard MQTT connection test with detailed logging."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        log.warning("paho-mqtt not installed, skipping MQTT test")
        return

    log.info("")
    log.info("="*60)
    log.info("TEST 2: MQTT connection")
    log.info("="*60)

    endpoint = creds.get("mqtt_endpoint", "")
    mqtt_client_id = creds.get("mqtt_client_id", "")
    username = creds.get("mqtt_username", "")
    password = creds.get("mqtt_password", "")
    pub_topic = creds.get("mqtt_publish_topic", "")

    # Derive subscribe topic
    parts = mqtt_client_id.split("@@@")
    if len(parts) >= 2:
        device_tag = parts[1]
        sub_topic = f"devices/p2p/{device_tag}"
    else:
        sub_topic = creds.get("mqtt_subscribe_topic", "")

    log.info("  endpoint: %s", endpoint)
    log.info("  client_id: %s", mqtt_client_id[:60])
    log.info("  pub_topic: %s", pub_topic)
    log.info("  sub_topic: %s", sub_topic)

    host = endpoint
    port = 8883
    if ":" in endpoint:
        host, port_str = endpoint.rsplit(":", 1)
        port = int(port_str)

    loop = asyncio.get_running_loop()
    results = {"messages": [], "connected": False}
    done = loop.create_future()
    t0 = time.monotonic()

    hello = json.dumps({
        "type": "hello",
        "version": 3,
        "features": {"mcp": True},
        "transport": "udp",
        "audio_params": {
            "format": "opus",
            "sample_rate": 16000,
            "channels": 1,
            "frame_duration": 60,
        },
    })

    def on_connect(client, userdata, flags, rc):
        elapsed = (time.monotonic() - t0) * 1000
        log.info("[+%.0fms] MQTT CONNACK rc=%d (%s)", elapsed, rc,
                 mqtt.connack_string(rc) if hasattr(mqtt, 'connack_string') else str(rc))
        if rc == 0:
            results["connected"] = True
            # Subscribe to P2P and also wildcard for discovery
            client.subscribe(sub_topic, qos=0)
            log.info("[+%.0fms] Subscribed to %s", elapsed, sub_topic)
            # Also subscribe to wildcard to catch any messages
            client.subscribe("#", qos=0)
            log.info("[+%.0fms] Subscribed to # (wildcard)", elapsed)
            # Send hello
            result = client.publish(pub_topic, hello, qos=0)
            elapsed2 = (time.monotonic() - t0) * 1000
            log.info("[+%.0fms] Published hello (mid=%s)", elapsed2, result.mid)
        else:
            loop.call_soon_threadsafe(
                lambda: done.set_result(False) if not done.done() else None
            )

    def on_message(client, userdata, msg):
        elapsed = (time.monotonic() - t0) * 1000
        payload = msg.payload.decode("utf-8", errors="replace")
        log.info("[+%.0fms] MQTT msg [%s]: %s", elapsed, msg.topic, payload[:500])
        results["messages"].append({"t_ms": elapsed, "topic": msg.topic, "payload": payload})

        try:
            data = json.loads(payload)
            if data.get("type") == "hello":
                log.info("  >>> GOT HELLO RESPONSE! session_id=%s", data.get("session_id"))
                log.info("  >>> Transport: %s", data.get("transport"))
                if data.get("udp"):
                    log.info("  >>> UDP: server=%s port=%s", data["udp"].get("server"), data["udp"].get("port"))
            elif data.get("type") == "goodbye":
                log.info("  >>> GOT GOODBYE session_id=%s", data.get("session_id"))
            elif data.get("type") == "mcp":
                method = data.get("payload", {}).get("method", "")
                rpc_id = data.get("payload", {}).get("id")
                log.info("  >>> GOT MCP method=%s id=%s", method, rpc_id)

                # Respond to MCP initialize (gateway needs this!)
                if method == "initialize" and rpc_id:
                    response = json.dumps({
                        "type": "mcp",
                        "payload": {
                            "jsonrpc": "2.0",
                            "id": rpc_id,
                            "result": {
                                "protocolVersion": "2024-11-05",
                                "capabilities": {},
                                "serverInfo": {"name": "whisplay-diag", "version": "1.0.0"}
                            }
                        }
                    })
                    client.publish(pub_topic, response, qos=0)
                    elapsed2 = (time.monotonic() - t0) * 1000
                    log.info("[+%.0fms] Sent MCP initialize response", elapsed2)

                # Respond to MCP tools/list
                if method == "tools/list" and rpc_id:
                    response = json.dumps({
                        "type": "mcp",
                        "payload": {
                            "jsonrpc": "2.0",
                            "id": rpc_id,
                            "result": {"tools": []}
                        }
                    })
                    client.publish(pub_topic, response, qos=0)
                    elapsed2 = (time.monotonic() - t0) * 1000
                    log.info("[+%.0fms] Sent MCP tools/list response", elapsed2)

                # Respond to notifications/initialized (no response needed, just log)
                if method == "notifications/initialized":
                    log.info("  >>> MCP initialized notification (no response needed)")

            elif data.get("type") == "error":
                log.info("  >>> GOT ERROR: %s", data.get("message"))
        except json.JSONDecodeError:
            log.info("  >>> Non-JSON payload")

    def on_disconnect(client, userdata, rc):
        elapsed = (time.monotonic() - t0) * 1000
        log.info("[+%.0fms] MQTT disconnected rc=%d", elapsed, rc)
        loop.call_soon_threadsafe(
            lambda: done.set_result(True) if not done.done() else None
        )

    # Create client
    try:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=mqtt_client_id,
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=False,
        )
    except (AttributeError, TypeError):
        client = mqtt.Client(
            client_id=mqtt_client_id,
            protocol=mqtt.MQTTv311,
        )

    client.username_pw_set(username, password)
    if port == 8883:
        client.tls_set(cert_reqs=mqtt.ssl.CERT_REQUIRED, tls_version=mqtt.ssl.PROTOCOL_TLS)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    log.info("Connecting to MQTT %s:%d ...", host, port)
    client.connect_async(host, port, keepalive=60)
    client.loop_start()

    # Wait up to 20 seconds for results
    try:
        await asyncio.wait_for(asyncio.shield(done), timeout=20.0)
    except asyncio.TimeoutError:
        pass

    # Give a moment for any final messages
    await asyncio.sleep(2)

    client.loop_stop()
    try:
        client.disconnect()
    except Exception:
        pass

    log.info("")
    log.info("--- MQTT Test Summary ---")
    log.info("Connected: %s", results["connected"])
    log.info("Messages received: %d", len(results["messages"]))
    for m in results["messages"]:
        log.info("  [+%.0fms] %s: %s", m["t_ms"], m["topic"], m["payload"][:200])


# ===================== Main =====================
async def main():
    creds = load_creds()
    log.info("Loaded credentials (device_id=%s)", creds.get("device_id", "?"))

    # Test 1: Direct WebSocket
    await test_direct_websocket(creds)

    # Small pause between tests
    await asyncio.sleep(2)

    # Test 2: MQTT
    await test_mqtt(creds)

    log.info("")
    log.info("="*60)
    log.info("DIAGNOSTIC COMPLETE")
    log.info("="*60)


if __name__ == "__main__":
    asyncio.run(main())
