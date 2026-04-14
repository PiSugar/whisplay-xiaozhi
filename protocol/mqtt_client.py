"""
XiaoZhi MQTT protocol client.

MQTT transport for XiaoZhi server:
- Connect to MQTT broker with OTA-provided credentials
- JSON messages over MQTT publish/subscribe topics
- Audio streaming over encrypted UDP (AES-CTR)
- Same callback interface as XiaoZhiClient (WebSocket)

Reference: py-xiaozhi mqtt_protocol.py
"""

import asyncio
import json
import logging
import socket
import threading
import uuid

import paho.mqtt.client as mqtt
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import config

log = logging.getLogger("protocol.mqtt")

# Emotion name → emoji character mapping (from XiaoZhi protocol docs)
_EMOTION_TO_EMOJI = {
    "neutral": "😶", "happy": "🙂", "laughing": "😆", "funny": "😂",
    "sad": "😔", "angry": "😠", "crying": "😭", "loving": "😍",
    "embarrassed": "😳", "surprised": "😲", "shocked": "😱", "thinking": "🤔",
    "winking": "😉", "cool": "😎", "relaxed": "😌", "delicious": "🤤",
    "kissy": "😘", "confident": "😏", "sleepy": "😴", "silly": "😜",
    "confused": "🙄", "smile": "😊",
}


class XiaoZhiMqttClient:
    """Async XiaoZhi MQTT + UDP client."""

    def __init__(self):
        self._mqtt: mqtt.Client | None = None
        self._session_id: str | None = None
        self._connected = False
        self._is_closing = False

        # MQTT config (set before connect)
        self.endpoint: str = ""
        self.mqtt_client_id: str = ""
        self.username: str = ""
        self.password: str = ""
        self.publish_topic: str = ""
        self.subscribe_topic: str | None = None

        # Device identity
        self.device_id: str = ""
        self.client_id: str = ""

        # UDP config (populated from server hello)
        self._udp_socket: socket.socket | None = None
        self._udp_thread: threading.Thread | None = None
        self._udp_running = False
        self._udp_server: str = ""
        self._udp_port: int = 0
        self._aes_key: str = ""
        self._aes_nonce: str = ""
        self._local_seq: int = 0

        # Async event loop reference
        self._loop: asyncio.AbstractEventLoop | None = None
        self._hello_event: asyncio.Event | None = None
        self._mcp_complete: asyncio.Event | None = None
        self._pending_messages: list[dict] = []

        # Callbacks — same interface as XiaoZhiClient
        self.on_stt = None
        self.on_llm_emotion = None
        self.on_tts_start = None
        self.on_tts_audio = None
        self.on_tts_sentence_start = None
        self.on_tts_stop = None
        self.on_listen_stop = None
        self.on_mcp = None
        self.on_iot = None
        self.on_disconnected = None

    @property
    def connected(self) -> bool:
        return self._connected

    # ==================== Connection ====================
    async def connect(self):
        """Connect to MQTT broker and perform hello handshake."""
        if not self.endpoint or not self.username or not self.password:
            raise ValueError("MQTT credentials must be set before connect")
        if not self.publish_topic:
            raise ValueError("publish_topic must be set before connect")

        self._loop = asyncio.get_running_loop()
        self._hello_event = asyncio.Event()
        self._mcp_complete = asyncio.Event()
        self._is_closing = False
        self._pending_messages = []

        # Parse endpoint
        host, port = self._parse_endpoint(self.endpoint)
        use_tls = (port == 8883)

        # Cleanup old client
        if self._mqtt:
            try:
                self._mqtt.loop_stop()
                self._mqtt.disconnect()
            except Exception:
                pass
            self._mqtt = None

        # Handle subscribe_topic "null" string (server convention)
        # Alibaba Cloud MQ uses P2P mechanism to deliver messages directly
        # to the target client without explicit subscription (matching py-xiaozhi)
        effective_subscribe = self.subscribe_topic
        if not effective_subscribe:
            effective_subscribe = None
            log.info("no subscribe_topic, relying on P2P auto-delivery")
        elif effective_subscribe == "null":
            log.info("subscribe_topic is 'null', subscribing to literal topic (matching py-xiaozhi)")

        # Create MQTT client (compatible with paho-mqtt v1 and v2)
        # Must use MQTT v3.1.1 (xiaozhi broker doesn't support v5)
        # Disable auto-reconnect — we handle reconnects at the application level
        try:
            # paho-mqtt v2 API
            self._mqtt = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id=self.mqtt_client_id,
                protocol=mqtt.MQTTv311,
                reconnect_on_failure=False,
            )
        except (AttributeError, TypeError):
            # paho-mqtt v1 API
            self._mqtt = mqtt.Client(
                client_id=self.mqtt_client_id,
                protocol=mqtt.MQTTv311,
            )

        self._mqtt.username_pw_set(self.username, self.password)

        if use_tls:
            self._mqtt.tls_set(
                ca_certs=None,
                certfile=None,
                keyfile=None,
                cert_reqs=mqtt.ssl.CERT_REQUIRED,
                tls_version=mqtt.ssl.PROTOCOL_TLS,
            )
            log.info("TLS enabled")

        # Setup callbacks
        connect_future = self._loop.create_future()

        # Pre-build hello message
        hello = json.dumps({
            "type": "hello",
            "version": 3,
            "transport": "udp",
            "features": {"mcp": True},
            "audio_params": {
                "format": "opus",
                "sample_rate": config.AUDIO_INPUT_SAMPLE_RATE,
                "channels": 1,
                "frame_duration": 60,
            },
        })

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                log.info("MQTT connected to %s:%d", host, port)
                # Subscribe immediately in on_connect callback (per paho best practice)
                if effective_subscribe:
                    client.subscribe(effective_subscribe, qos=1)
                    log.info("subscribed to %s (qos=1)", effective_subscribe)
                # Do NOT send hello here — wait for gateway's MCP handshake
                # to complete first.  The gateway runs initializeDeviceTools()
                # on MQTT connect, caching our MCP tool list.  If we send
                # hello before the cache is ready, the gateway's bridge to
                # the chat server fails (chat server gets empty MCP data and
                # sends goodbye instead of hello).
                self._loop.call_soon_threadsafe(
                    lambda: connect_future.set_result(True) if not connect_future.done() else None
                )
            else:
                log.error("MQTT connect failed, rc=%d", rc)
                self._loop.call_soon_threadsafe(
                    lambda: connect_future.set_exception(
                        ConnectionError(f"MQTT connect failed, rc={rc}")
                    ) if not connect_future.done() else None
                )

        def on_message(client, userdata, msg):
            try:
                payload = msg.payload.decode("utf-8")
                log.info("MQTT recv [%s]: %s", msg.topic, payload[:300])
                self._handle_mqtt_message(payload)
            except Exception as e:
                log.error("MQTT message error: %s", e)

        def on_disconnect(client, userdata, rc):
            was_connected = self._connected
            self._connected = False
            if rc != 0:
                log.warning("MQTT disconnected unexpectedly, rc=%d", rc)
            else:
                log.info("MQTT disconnected normally")
            if was_connected and not self._is_closing and self.on_disconnected:
                self._loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self.on_disconnected())
                )

        self._mqtt.on_connect = on_connect
        self._mqtt.on_message = on_message
        self._mqtt.on_disconnect = on_disconnect

        # Connect
        log.info("connecting to MQTT %s:%d (client_id=%s)", host, port, self.mqtt_client_id[:40])
        self._mqtt.connect_async(host, port, keepalive=60)
        self._mqtt.loop_start()

        # Wait for connection
        try:
            await asyncio.wait_for(connect_future, timeout=15.0)
        except asyncio.TimeoutError:
            log.error("MQTT connection timeout")
            self._mqtt.loop_stop()
            raise ConnectionError("MQTT connection timeout")

        # Wait for gateway's MCP handshake to complete before sending hello.
        # The gateway calls initializeDeviceTools() on MQTT connect which
        # sends MCP initialize + tools/list requests.  We must respond to
        # those BEFORE sending hello so the gateway has our tool cache ready
        # when it creates the WebSocket bridge to the chat server.
        log.info("waiting for gateway MCP handshake...")
        try:
            await asyncio.wait_for(self._mcp_complete.wait(), timeout=5.0)
            log.info("MCP handshake complete, sending hello")
        except asyncio.TimeoutError:
            log.warning("MCP handshake timeout (gateway may not support MCP), sending hello anyway")

        # Now send hello
        result = self._mqtt.publish(self.publish_topic, hello, qos=0)
        log.info("hello published (mid=%s)", result.mid)

        # Wait for server hello
        log.info("waiting for server hello...")
        try:
            await asyncio.wait_for(self._hello_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            log.error("server hello timeout")
            self._mqtt.loop_stop()
            self._mqtt.disconnect()
            raise ConnectionError("Server did not respond to hello")

        # Setup UDP socket
        self._stop_udp()
        self._udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp_socket.settimeout(0.5)

        self._udp_running = True
        self._udp_thread = threading.Thread(target=self._udp_receive_loop, daemon=True)
        self._udp_thread.start()

        self._connected = True
        log.info("connected, session=%s, udp=%s:%d", self._session_id, self._udp_server, self._udp_port)

        # Flush messages that arrived during hello handshake
        pending = self._pending_messages
        self._pending_messages = []
        for msg in pending:
            log.debug("dispatching queued message: type=%s", msg.get("type"))
            self._dispatch_json(msg)

    async def disconnect(self):
        """Close MQTT connection and UDP socket."""
        self._is_closing = True
        self._connected = False
        self._session_id = None

        self._stop_udp()

        if self._mqtt:
            try:
                self._mqtt.loop_stop()
                self._mqtt.disconnect()
            except Exception:
                pass
            self._mqtt = None

        self._is_closing = False
        log.info("disconnected")

    # ==================== MQTT message handling ====================
    def _handle_mqtt_message(self, payload: str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            log.warning("invalid JSON: %s", payload[:200])
            return

        msg_type = data.get("type")

        if msg_type == "hello":
            # Server hello — extract UDP config
            transport = data.get("transport")
            if transport != "udp":
                log.error("unsupported transport: %s", transport)
                return

            self._session_id = data.get("session_id", "")
            udp = data.get("udp")
            if not udp:
                log.error("missing UDP config in hello")
                return

            self._udp_server = udp.get("server", "")
            self._udp_port = udp.get("port", 0)
            self._aes_key = udp.get("key", "")
            self._aes_nonce = udp.get("nonce", "")
            self._local_seq = 0

            log.info("server hello: UDP %s:%d", self._udp_server, self._udp_port)
            if self._loop and self._hello_event:
                self._loop.call_soon_threadsafe(self._hello_event.set)

            return

        if msg_type == "goodbye":
            session_id = data.get("session_id")
            # Only process goodbye if we have an active session and it matches
            # (ignore stale goodbyes from previous sessions delivered via P2P cache)
            if self._session_id and (not session_id or session_id == self._session_id):
                log.info("received goodbye (session=%s)", session_id)
                if self._loop and self.on_disconnected:
                    self._loop.call_soon_threadsafe(
                        lambda: asyncio.ensure_future(self.on_disconnected())
                    )
            else:
                log.warning("ignoring goodbye before hello (our_session=%s, msg_session=%s)", self._session_id, session_id)
            return

        # MCP messages must be dispatched immediately — even during hello
        # handshake.  The server sends MCP initialize before hello and expects
        # a response before it will send hello back.
        if msg_type == "mcp":
            self._dispatch_json(data)
            return

        # Queue other non-hello/goodbye messages until hello handshake completes.
        if not self._connected:
            self._pending_messages.append(data)
            log.debug("queued message (hello pending): type=%s", msg_type)
            return
        self._dispatch_json(data)

    def _dispatch_json(self, data: dict):
        """Dispatch JSON message to appropriate callback (runs in MQTT thread)."""
        msg_type = data.get("type")

        def _run(coro):
            if self._loop and coro:
                self._loop.call_soon_threadsafe(lambda: asyncio.ensure_future(coro))

        if msg_type == "stt":
            text = data.get("text", "")
            if self.on_stt:
                _run(self.on_stt(text))

        elif msg_type == "listen":
            state = data.get("state")
            if state == "stop" and self.on_listen_stop:
                _run(self.on_listen_stop())

        elif msg_type == "llm":
            # Server sends {"type":"llm", "text":"😊", "emotion":"smile"}
            # Prefer the emoji character from "text", fall back to mapping "emotion" name
            emoji = data.get("text") or _EMOTION_TO_EMOJI.get(data.get("emotion", ""), data.get("emotion"))
            if emoji and self.on_llm_emotion:
                _run(self.on_llm_emotion(emoji))

        elif msg_type == "tts":
            state = data.get("state")
            if state == "start" and self.on_tts_start:
                _run(self.on_tts_start())
            elif state == "sentence_start":
                text = data.get("text", "")
                if self.on_tts_sentence_start:
                    _run(self.on_tts_sentence_start(text))
            elif state == "stop" and self.on_tts_stop:
                _run(self.on_tts_stop())

        elif msg_type == "mcp":
            if self.on_mcp:
                _run(self.on_mcp(data))

        elif msg_type == "iot":
            commands = data.get("commands", [])
            if commands and self.on_iot:
                _run(self.on_iot(commands))

        else:
            log.debug("unhandled message type: %s", msg_type)

    # ==================== UDP audio ====================
    def _udp_receive_loop(self):
        """Receive encrypted audio from UDP and dispatch to callback."""
        log.info("UDP receive thread started, %s:%d", self._udp_server, self._udp_port)
        while self._udp_running:
            try:
                data, addr = self._udp_socket.recvfrom(4096)
                if len(data) < 16:
                    continue

                # Split nonce (16 bytes) + encrypted audio
                received_nonce = data[:16]
                encrypted_audio = data[16:]

                # AES-CTR decrypt
                decrypted = self._aes_ctr_decrypt(
                    bytes.fromhex(self._aes_key),
                    received_nonce,
                    encrypted_audio,
                )

                if self.on_tts_audio and self._loop:
                    self._loop.call_soon_threadsafe(
                        lambda d=decrypted: asyncio.ensure_future(self.on_tts_audio(d))
                    )

            except socket.timeout:
                continue
            except Exception as e:
                if not self._udp_running:
                    break
                log.error("UDP receive error: %s", e)

        log.info("UDP receive thread stopped")

    def _stop_udp(self):
        """Stop UDP receiver thread and close socket."""
        self._udp_running = False
        if self._udp_thread and self._udp_thread.is_alive():
            self._udp_thread.join(1.0)
            self._udp_thread = None
        if self._udp_socket:
            try:
                self._udp_socket.close()
            except Exception:
                pass
            self._udp_socket = None

    # ==================== Send commands ====================
    async def send_listen_start(self, mode: str = "auto"):
        await self._send_json({
            "session_id": self._session_id,
            "type": "listen",
            "state": "start",
            "mode": mode,
        })

    async def send_listen_stop(self):
        await self._send_json({
            "session_id": self._session_id,
            "type": "listen",
            "state": "stop",
        })

    async def send_audio(self, opus_frame: bytes):
        """Send Opus audio frame over encrypted UDP."""
        if not self._udp_socket or not self._udp_server or not self._udp_port:
            return
        try:
            self._local_seq = (self._local_seq + 1) & 0xFFFFFFFF
            new_nonce = (
                self._aes_nonce[:4]
                + format(len(opus_frame), "04x")
                + self._aes_nonce[8:24]
                + format(self._local_seq, "08x")
            )

            encrypted = self._aes_ctr_encrypt(
                bytes.fromhex(self._aes_key),
                bytes.fromhex(new_nonce),
                bytes(opus_frame),
            )

            packet = bytes.fromhex(new_nonce) + encrypted
            self._udp_socket.sendto(packet, (self._udp_server, self._udp_port))
        except Exception as e:
            log.error("UDP send error: %s", e)

    async def send_abort(self):
        await self._send_json({
            "session_id": self._session_id,
            "type": "abort",
        })

    async def send_mcp_response(self, mcp_id: str, result: dict):
        await self._send_json({
            "type": "mcp",
            "payload": {
                "jsonrpc": "2.0",
                "id": mcp_id,
                "result": result,
            },
        })

    async def send_mcp_notification(self, method: str, params: dict | None = None):
        """Send an MCP notification (no id, no response expected)."""
        payload: dict = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        await self._send_json({"type": "mcp", "payload": payload})

    async def send_iot_descriptors(self, descriptors: list):
        await self._send_json({
            "session_id": self._session_id,
            "type": "iot",
            "descriptors": descriptors,
        })

    async def receive_loop(self):
        """Keep alive until disconnect. Paho loop_start handles actual receiving."""
        try:
            while self._mqtt and not self._is_closing:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    def mark_mcp_complete(self):
        """Signal that MCP handshake is done, so hello can be sent."""
        log.info("MCP handshake complete, unblocking hello")
        if self._loop and self._mcp_complete:
            self._loop.call_soon_threadsafe(self._mcp_complete.set)

    # ==================== Internals ====================
    async def _send_json(self, obj: dict):
        self._mqtt_publish(json.dumps(obj))

    def _mqtt_publish(self, message: str) -> bool:
        if self._mqtt and self.publish_topic:
            try:
                log.info("MQTT send [%s]: %s", self.publish_topic, message[:300])
                result = self._mqtt.publish(self.publish_topic, message, qos=0)
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    log.error("MQTT publish error rc=%d", result.rc)
                    return False
                return True
            except Exception as e:
                log.error("MQTT publish failed: %s", e)
                return False
        return False

    @staticmethod
    def _parse_endpoint(endpoint: str) -> tuple:
        if ":" in endpoint:
            host, port_str = endpoint.rsplit(":", 1)
            return host, int(port_str)
        return endpoint, 8883

    @staticmethod
    def _aes_ctr_encrypt(key: bytes, nonce: bytes, plaintext: bytes) -> bytes:
        cipher = Cipher(algorithms.AES(key), modes.CTR(nonce))
        encryptor = cipher.encryptor()
        return encryptor.update(plaintext) + encryptor.finalize()

    @staticmethod
    def _aes_ctr_decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
        cipher = Cipher(algorithms.AES(key), modes.CTR(nonce))
        decryptor = cipher.decryptor()
        return decryptor.update(ciphertext) + decryptor.finalize()

    # Stub for receive_loop compatibility with WebSocket client
    async def receive_loop(self):
        """MQTT receive is handled by paho's loop_start(), this just keeps alive."""
        try:
            while self._connected and not self._is_closing:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
