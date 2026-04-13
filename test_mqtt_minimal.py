"""Minimal test: MQTT connect + hello, log all messages."""
import asyncio
import json
import logging
import os
import uuid

import paho.mqtt.client as mqtt
import requests

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("test")


def get_mac():
    try:
        with open("/sys/class/net/wlan0/address") as f:
            return f.read().strip().upper()
    except Exception:
        return "00:00:00:00:00:00"


def ota_check(mac):
    # Use the saved client_id from activation, not a random one!
    cred_file = os.path.expanduser("~/whisplay-xiaozhi/data/credentials.json")
    try:
        with open(cred_file) as f:
            creds = json.load(f)
        client_id = creds.get("client_id", str(uuid.uuid4()))
        log.info("using saved client_id: %s", client_id)
    except Exception:
        client_id = str(uuid.uuid4())
        log.warning("no saved credentials, using random client_id: %s", client_id)

    url = "https://api.tenclass.net/xiaozhi/ota/"
    headers = {
        "Activation-Version": "1",
        "Device-Id": mac,
        "Client-Id": client_id,
        "User-Agent": "whisplay-xiaozhi/1.0.0",
        "Accept-Language": "zh-CN",
        "Content-Type": "application/json",
    }
    body = {
        "version": 2,
        "language": "zh-CN",
        "flash_size": 0,
        "minimum_free_heap_size": "0",
        "mac_address": mac,
        "uuid": client_id,
        "chip_model_name": "Raspberry Pi",
        "chip_info": {"model": 0, "cores": 4, "revision": 0, "features": 0},
        "application": {
            "name": "whisplay-xiaozhi",
            "version": "1.0.0",
            "compile_time": "",
            "idf_version": "python3.13",
            "elf_sha256": "",
        },
        "board": {"type": "whisplay-xiaozhi", "name": "whisplay-xiaozhi"},
    }
    resp = requests.post(url, headers=headers, json=body, timeout=15)
    data = resp.json()
    log.info("OTA: %s", json.dumps(data, ensure_ascii=False)[:500])
    return data.get("mqtt", {}), client_id


async def main():
    mac = get_mac()
    log.info("MAC: %s", mac)

    mqtt_info, client_id = ota_check(mac)
    if not mqtt_info:
        log.error("No MQTT info")
        return

    endpoint = mqtt_info["endpoint"]
    mqtt_client_id = mqtt_info["client_id"]
    username = mqtt_info["username"]
    password = mqtt_info["password"]
    publish_topic = mqtt_info["publish_topic"]
    subscribe_topic = mqtt_info.get("subscribe_topic")

    log.info("endpoint=%s client_id=%s", endpoint, mqtt_client_id[:50])
    log.info("pub=%s sub=%s", publish_topic, subscribe_topic)

    loop = asyncio.get_running_loop()
    connect_event = asyncio.Event()
    hello_event = asyncio.Event()
    messages = []

    # Create client matching py-xiaozhi style
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
        client_id=mqtt_client_id,
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(username, password)
    client.tls_set(
        cert_reqs=mqtt.ssl.CERT_REQUIRED,
        tls_version=mqtt.ssl.PROTOCOL_TLS,
    )

    def on_connect(c, ud, flags, rc):
        log.info("on_connect rc=%d flags=%s", rc, flags)
        if rc == 0:
            loop.call_soon_threadsafe(connect_event.set)

    def on_message(c, ud, msg):
        payload = msg.payload.decode()
        log.info("MSG topic=%s payload=%s", msg.topic, payload[:300])
        messages.append(payload)
        try:
            data = json.loads(payload)
            msg_type = data.get("type")

            if msg_type == "hello":
                loop.call_soon_threadsafe(hello_event.set)

            elif msg_type == "mcp":
                # Log MCP but DON'T respond - test if hello works without MCP interference
                rpc = data.get("payload", {})
                rpc_id = rpc.get("id")
                method = rpc.get("method", "")
                log.info("MCP method=%s id=%s (NOT responding)", method, rpc_id)

            elif msg_type == "goodbye":
                session_id = data.get("session_id")
                log.warning("GOT GOODBYE session_id=%s (ignoring stale)", session_id)
                loop.call_soon_threadsafe(goodbye_event.set)

        except Exception as e:
            log.error("parse error: %s", e)

    def on_disconnect(c, ud, rc):
        log.warning("on_disconnect rc=%d", rc)

    def on_publish(c, ud, mid):
        log.info("on_publish mid=%d", mid)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    client.on_publish = on_publish

    mcp_done_event = asyncio.Event()
    goodbye_event = asyncio.Event()

    log.info("connecting to %s:8883", endpoint)
    client.connect_async(endpoint, 8883, keepalive=60)
    client.loop_start()

    try:
        await asyncio.wait_for(connect_event.wait(), timeout=10)
        log.info("MQTT connected!")
    except asyncio.TimeoutError:
        log.error("connect timeout")
        client.loop_stop()
        return

    if subscribe_topic and subscribe_topic != "null":
        client.subscribe(subscribe_topic, qos=0)

    # Strategy: send hello IMMEDIATELY like py-xiaozhi does, DON'T respond to MCP
    hello = json.dumps({
        "type": "hello",
        "version": 3,
        "transport": "udp",
        "features": {"mcp": True},
        "audio_params": {
            "format": "opus",
            "sample_rate": 24000,
            "channels": 1,
            "frame_duration": 60,
        },
    })
    log.info("publishing hello immediately: %s", hello)
    result = client.publish(publish_topic, hello, qos=0)
    result.wait_for_publish()
    log.info("hello published and confirmed, rc=%d mid=%d", result.rc, result.mid)

    # Wait for server hello
    log.info("waiting for server hello (not responding to MCP)...")
    try:
        await asyncio.wait_for(hello_event.wait(), timeout=20)
        log.info("=== GOT SERVER HELLO! ===")
    except asyncio.TimeoutError:
        log.error("server hello timeout!")
        log.info("messages so far: %d", len(messages))
        for i, m in enumerate(messages):
            log.info("msg[%d]: %s", i, m[:300])

    # Wait for more messages
    await asyncio.sleep(3)
    log.info("total messages: %d", len(messages))
    for i, m in enumerate(messages):
        log.info("msg[%d]: %s", i, m[:300])

    client.loop_stop()
    client.disconnect()
    log.info("done")


if __name__ == "__main__":
    asyncio.run(main())
