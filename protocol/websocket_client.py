"""
XiaoZhi WebSocket protocol client.

Implements the full XiaoZhi device protocol:
- Hello handshake → session_id exchange
- listen (start/stop) + Opus audio streaming
- Receives: stt, llm, tts, mcp, system JSON messages + binary Opus TTS
- Supports abort (stop current response)

Reference: https://github.com/78/xiaozhi-esp32 websocket.md
"""

import asyncio
import json
import logging
import uuid

import websockets
import websockets.exceptions

import config

log = logging.getLogger("protocol")


class XiaoZhiClient:
    """Async XiaoZhi WebSocket client."""

    def __init__(self):
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._session_id: str | None = None
        self._connected = False

        # Connection credentials (set before connect)
        self.ws_url: str = ""
        self.ws_token: str = ""
        self.device_id: str = ""
        self.client_id: str = ""

        # Callbacks — set by Application
        self.on_stt: asyncio.coroutine | None = None       # (text: str)
        self.on_llm_emotion: asyncio.coroutine | None = None  # (emoji: str)
        self.on_tts_start: asyncio.coroutine | None = None
        self.on_tts_audio: asyncio.coroutine | None = None  # (opus_bytes: bytes)
        self.on_tts_sentence_start: asyncio.coroutine | None = None  # (text: str)
        self.on_tts_stop: asyncio.coroutine | None = None
        self.on_listen_stop: asyncio.coroutine | None = None  # server VAD stop
        self.on_mcp: asyncio.coroutine | None = None        # (payload: dict)
        self.on_iot: asyncio.coroutine | None = None         # (commands: list)
        self.on_disconnected: asyncio.coroutine | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    # ==================== Connection ====================
    async def connect(self):
        """Connect to XiaoZhi server and perform hello handshake."""
        if not self.ws_url or not self.ws_token:
            raise ValueError("ws_url and ws_token must be set before connect")
        headers = {
            "Authorization": f"Bearer {self.ws_token}",
            "Protocol-Version": "1",
            "Device-Id": self.device_id or config.DEVICE_ID or self._get_mac(),
            "Client-Id": self.client_id or config.CLIENT_ID,
        }
        log.info("connecting to %s", self.ws_url)
        try:
            self._ws = await websockets.connect(
                self.ws_url,
                additional_headers=headers,
                ping_interval=30,
                ping_timeout=10,
                max_size=2**20,
            )
        except Exception as e:
            log.error("connection failed: %s", e)
            raise

        # Send hello
        hello = {
            "type": "hello",
            "version": 1,
            "transport": "websocket",
            "audio_params": {
                "format": "opus",
                "sample_rate": config.AUDIO_INPUT_SAMPLE_RATE,
                "channels": 1,
                "frame_duration": 60,
            },
        }
        await self._send_json(hello)
        log.info("hello sent, waiting for server hello...")

        # Wait for server hello
        try:
            msg = await asyncio.wait_for(self._ws.recv(), timeout=10)
        except asyncio.TimeoutError:
            log.error("hello timeout")
            await self.disconnect()
            raise ConnectionError("Server did not respond to hello")

        if isinstance(msg, str):
            data = json.loads(msg)
            if data.get("type") == "hello":
                self._session_id = data.get("session_id")
                self._connected = True
                log.info("connected, session_id=%s", self._session_id)
            else:
                log.error("unexpected hello response: %s", data)
                await self.disconnect()
                raise ConnectionError(f"Unexpected response: {data}")
        else:
            await self.disconnect()
            raise ConnectionError("Expected text hello, got binary")

    async def disconnect(self):
        """Close WebSocket connection."""
        self._connected = False
        self._session_id = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        log.info("disconnected")

    # ==================== Receive loop ====================
    async def receive_loop(self):
        """Main receive loop — dispatches messages to callbacks."""
        if not self._ws:
            return
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    # Binary = Opus TTS audio frame
                    if self.on_tts_audio:
                        await self.on_tts_audio(message)
                elif isinstance(message, str):
                    await self._handle_json(message)
        except websockets.exceptions.ConnectionClosed as e:
            log.warning("connection closed: %s", e)
        except Exception as e:
            log.error("receive error: %s", e)
        finally:
            self._connected = False
            if self.on_disconnected:
                await self.on_disconnected()

    async def _handle_json(self, raw: str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("invalid JSON: %s", raw[:200])
            return

        msg_type = data.get("type")

        if msg_type == "stt":
            text = data.get("text", "")
            if self.on_stt:
                await self.on_stt(text)

        elif msg_type == "listen":
            state = data.get("state")
            if state == "stop" and self.on_listen_stop:
                await self.on_listen_stop()

        elif msg_type == "llm":
            emoji = data.get("emotion")
            if emoji and self.on_llm_emotion:
                await self.on_llm_emotion(emoji)

        elif msg_type == "tts":
            state = data.get("state")
            if state == "start":
                if self.on_tts_start:
                    await self.on_tts_start()
            elif state == "sentence_start":
                text = data.get("text", "")
                if self.on_tts_sentence_start:
                    await self.on_tts_sentence_start(text)
            elif state == "stop":
                if self.on_tts_stop:
                    await self.on_tts_stop()

        elif msg_type == "mcp":
            if self.on_mcp:
                await self.on_mcp(data)

        elif msg_type == "iot":
            commands = data.get("commands", [])
            if commands and self.on_iot:
                await self.on_iot(commands)

        elif msg_type == "hello":
            # Late hello (reconnect scenario)
            self._session_id = data.get("session_id")
            log.info("session_id updated: %s", self._session_id)

        else:
            log.debug("unhandled message type: %s", msg_type)

    # ==================== Send commands ====================
    async def send_listen_start(self, mode: str = "auto"):
        """Signal server that device started listening."""
        await self._send_json({
            "session_id": self._session_id,
            "type": "listen",
            "state": "start",
            "mode": mode,
        })

    async def send_listen_stop(self):
        """Signal server that device stopped listening."""
        await self._send_json({
            "session_id": self._session_id,
            "type": "listen",
            "state": "stop",
        })

    async def send_audio(self, opus_frame: bytes):
        """Send an Opus-encoded audio frame to the server."""
        if self._ws and self._connected:
            await self._ws.send(opus_frame)

    async def send_abort(self):
        """Abort current server response (e.g. button pressed during TTS)."""
        await self._send_json({
            "session_id": self._session_id,
            "type": "abort",
        })

    async def send_mcp_response(self, mcp_id: str, result: dict):
        """Send MCP tool call result back to the server."""
        await self._send_json({
            "session_id": self._session_id,
            "type": "mcp",
            "data": {
                "jsonrpc": "2.0",
                "id": mcp_id,
                "result": result,
            },
        })

    async def send_iot_descriptors(self, descriptors: list):
        """Send IoT thing descriptors to the server."""
        await self._send_json({
            "session_id": self._session_id,
            "type": "iot",
            "descriptors": descriptors,
        })

    # ==================== Internals ====================
    async def _send_json(self, obj: dict):
        if self._ws:
            await self._ws.send(json.dumps(obj))

    @staticmethod
    def _get_mac() -> str:
        """Get device MAC address as identifier."""
        try:
            with open("/sys/class/net/wlan0/address") as f:
                return f.read().strip().replace(":", "").upper()
        except Exception:
            pass
        try:
            with open("/sys/class/net/eth0/address") as f:
                return f.read().strip().replace(":", "").upper()
        except Exception:
            pass
        return uuid.uuid4().hex[:12].upper()
