"""
Application — main state machine orchestrating all subsystems.

States: activating → idle → connecting → listening → speaking → idle

Interaction model: push-to-wake (button press or wake word starts auto-listen,
server-side VAD controls when listening stops).

Device pairing: On first boot, the device contacts the OTA API to obtain a
verification code. The user enters this code on xiaozhi.me to bind the device.
Once paired, websocket credentials are stored locally for future connections.

Coordinates: WhisplayBoard, UIRenderer, AudioRecorder, AudioPlayer,
             OpusEncoder/Decoder, XiaoZhiClient, OtaClient,
             BatteryMonitor, LedController
"""

import asyncio
import logging
import signal

import config
from hardware.whisplay_board import WhisplayBoard
from hardware.battery import BatteryMonitor
from hardware.led_controller import LedController
from display.ui_renderer import UIRenderer
from audio.audio_codec import OpusEncoder, OpusDecoder
from audio.audio_recorder import AudioRecorder
from audio.audio_player import AudioPlayer
from protocol.websocket_client import XiaoZhiClient
from protocol.mqtt_client import XiaoZhiMqttClient
from protocol.mcp_handler import McpHandler
from protocol.ota_client import OtaClient

log = logging.getLogger("app")


class Application:
    """Main application controller."""

    # States
    ACTIVATING = "activating"
    IDLE = "idle"
    CONNECTING = "connecting"
    LISTENING = "listening"
    SPEAKING = "speaking"

    def __init__(self):
        # Hardware
        self.board: WhisplayBoard | None = None
        self.led: LedController | None = None
        self.battery = BatteryMonitor()

        # Display
        self.display: UIRenderer | None = None

        # Audio
        self.recorder = AudioRecorder()
        self.player = AudioPlayer()
        self.encoder = OpusEncoder(frame_duration_ms=60)
        self.decoder = OpusDecoder(frame_duration_ms=60)

        # Protocol
        self.ota = OtaClient(
            ota_url=config.OTA_URL,
            device_id=config.DEVICE_ID,
            client_id=config.CLIENT_ID,
        )
        self.client = None  # Set in _activate_and_connect (WebSocket or MQTT)
        self.mcp = McpHandler()

        # State
        self._state = self.IDLE
        self._recording_task: asyncio.Task | None = None
        self._receive_task: asyncio.Task | None = None
        self._running = False
        self._tts_text_buffer: str = ""
        self._reconnecting = False  # Prevent concurrent reconnect storms
        self._loop: asyncio.AbstractEventLoop | None = None
        self._keep_listening = False  # Auto-restart listening after TTS
        self._listen_after_connect = False  # Auto-start listening after reconnect

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, new_state: str):
        if new_state != self._state:
            log.info("state: %s → %s", self._state, new_state)
            self._state = new_state
            if self.led:
                self.led.set_state(new_state)

    # ==================== Lifecycle ====================
    async def start(self):
        """Initialize hardware and start all subsystems."""
        self._running = True
        self._loop = asyncio.get_running_loop()

        # Init hardware
        try:
            self.board = WhisplayBoard()
            self.led = LedController(self.board)
            self.led.set_state(self.IDLE)
        except Exception as e:
            log.error("hardware init failed: %s", e)
            log.info("running without hardware (display/LED disabled)")

        # Init display
        if self.board:
            self.display = UIRenderer(self.board, font_path=config.FONT_PATH)
            self.display.start()
            self.board.set_backlight(config.LCD_BRIGHTNESS)

        # Setup button callbacks
        if self.board:
            self.board.on_button_press(self._on_button_press)
            self.board.on_button_release(self._on_button_release)

        # Start battery monitor
        await self.battery.start()

        # Battery display update loop
        asyncio.create_task(self._battery_display_loop())

        # Update display
        self._update_display(status="Whisplay XiaoZhi", text="Starting...")

        # Activate (pair) and connect
        await self._activate_and_connect()

        log.info("application started")

    async def stop(self):
        """Shutdown all subsystems."""
        if not self._running:
            return
        self._running = False
        self._keep_listening = False

        # Cancel tasks
        if self._recording_task:
            self._recording_task.cancel()
        if self._receive_task:
            self._receive_task.cancel()

        # Stop components
        self.recorder.stop()
        await self.player.stop()
        if self.client:
            await self.client.disconnect()
        await self.battery.stop()

        if self.display:
            self.display.stop()
        if self.board:
            self.board.set_backlight(0)
            self.board.cleanup()

        log.info("application stopped")

    # ==================== Activation & Connection ====================
    async def _activate_and_connect(self):
        """Ensure device is paired, then connect (WebSocket preferred, MQTT fallback).

        If XIAOZHI_WS_URL is configured, connect directly without OTA.
        Otherwise, use OTA to obtain credentials (MQTT credentials rotate per
        OTA request, so we always fetch fresh ones).
        """
        # Clean up old client before creating a new one
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

        # Cancel old receive task
        if self._receive_task:
            self._receive_task.cancel()
            self._receive_task = None

        # Direct WebSocket mode — bypass OTA entirely
        if config.WS_URL:
            log.info("using direct WebSocket: %s", config.WS_URL)
            client = XiaoZhiClient()
            client.ws_url = config.WS_URL
            client.ws_token = config.WS_TOKEN or ""
            client.device_id = config.DEVICE_ID or self.ota.device_id
            client.client_id = config.CLIENT_ID or self.ota.client_id
            self.client = client
            self._wire_callbacks()
            await self._connect()
            return

        # Always check with OTA server first to verify device is still paired.
        # If server says activation needed, saved credentials are stale.
        log.info("checking device status with OTA server...")
        try:
            paired = await asyncio.get_event_loop().run_in_executor(
                None, self.ota.check_version
            )
            if paired:
                log.info("OTA confirmed device is paired")
                # OtaClient.check_version already saved fresh credentials
                creds = OtaClient.load_credentials()
            else:
                # Server requires (re-)activation — clear stale credentials
                log.info("OTA requires activation, entering pairing flow")
                await self._run_activation()
                creds = OtaClient.load_credentials()
        except Exception as e:
            log.warning("OTA check failed: %s, falling back to saved credentials", e)
            creds = OtaClient.load_credentials()

        if not creds:
            log.error("no credentials available after activation")
            return

        # Choose protocol: WebSocket preferred (matching py-xiaozhi).
        # MQTT as fallback if no WebSocket credentials.
        if creds.get("ws_url"):
            ws_token = creds.get("ws_token") or "test-token"
            log.info("using WebSocket transport (url=%s)", creds["ws_url"])
            client = XiaoZhiClient()
            client.ws_url = creds["ws_url"]
            client.ws_token = ws_token
            client.device_id = creds.get("device_id", "")
            client.client_id = creds.get("client_id", "")
        elif creds.get("mqtt_endpoint") and creds.get("mqtt_publish_topic"):
            log.info("using MQTT transport (endpoint=%s)", creds["mqtt_endpoint"])
            client = XiaoZhiMqttClient()
            client.endpoint = creds["mqtt_endpoint"]
            client.mqtt_client_id = creds.get("mqtt_client_id", "")
            client.username = creds.get("mqtt_username", "")
            client.password = creds.get("mqtt_password", "")
            client.publish_topic = creds.get("mqtt_publish_topic", "")
            client.subscribe_topic = creds.get("mqtt_subscribe_topic")
            client.device_id = creds.get("device_id", "")
            client.client_id = creds.get("client_id", "")
        else:
            log.error("no valid transport credentials")
            return

        self.client = client
        self._wire_callbacks()
        await self._connect()

    def _wire_callbacks(self):
        """Wire protocol callbacks to application handlers."""
        self.client.on_stt = self._on_stt
        self.client.on_llm_emotion = self._on_llm_emotion
        self.client.on_tts_start = self._on_tts_start
        self.client.on_tts_audio = self._on_tts_audio
        self.client.on_tts_sentence_start = self._on_tts_sentence_start
        self.client.on_tts_stop = self._on_tts_stop
        self.client.on_listen_stop = self._on_listen_stop
        self.client.on_mcp = self._on_mcp
        self.client.on_iot = self._on_iot
        self.client.on_goodbye = self._on_goodbye
        self.client.on_disconnected = self._on_disconnected

    async def _run_activation(self):
        """OTA activation: get verification code, show on screen, poll until paired."""
        self._set_state(self.ACTIVATING)
        self._update_display(status="Activating...", emoji="🔗")

        retry_delay = 5
        while self._running:
            try:
                paired = await asyncio.get_event_loop().run_in_executor(
                    None, self.ota.check_version
                )
                if paired:
                    log.info("device already paired")
                    return

                if self.ota.activation_code:
                    code = self.ota.activation_code
                    msg = self.ota.activation_message or "Enter this code on xiaozhi.me"
                    log.info("activation code: %s", code)
                    self._update_display(
                        status="Pairing",
                        emoji="🔗",
                        text=f"Verification code:\n\n    {code}\n\n{msg}",
                    )

                    # Poll activate endpoint until success
                    await self._poll_activation()

                    # Re-check to get websocket config
                    paired = await asyncio.get_event_loop().run_in_executor(
                        None, self.ota.check_version
                    )
                    if paired:
                        log.info("pairing complete")
                        return
                else:
                    log.warning("no activation code and no ws config")

            except Exception as e:
                log.error("activation error: %s", e)
                self._update_display(
                    status="Activation failed", emoji="❌",
                    text=f"Error: {e}\nRetrying in {retry_delay}s...",
                )

            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)

    async def _poll_activation(self):
        """Poll the activate endpoint until user completes binding."""
        poll_interval = 3
        max_polls = 200  # ~10 min
        for i in range(max_polls):
            if not self._running:
                return
            result = await asyncio.get_event_loop().run_in_executor(
                None, self.ota.activate
            )
            if result == "ok":
                return
            elif result == "error":
                # Wait longer on errors
                await asyncio.sleep(10)
            else:
                # pending
                await asyncio.sleep(poll_interval)
        log.warning("activation polling timed out")

    async def _connect(self):
        """Connect to XiaoZhi server with retry."""
        self._set_state(self.CONNECTING)
        self._update_display(status="Connecting...", emoji="🔄")

        retry_delay = 2
        max_retries = 10
        attempt = 0
        while self._running and attempt < max_retries:
            attempt += 1
            try:
                await self.client.connect()
                self._set_state(self.IDLE)
                self._update_display(status="Connected", emoji="😄", text="Press button to wake...")

                # Start receive loop
                self._receive_task = asyncio.create_task(self.client.receive_loop())

                # If button was pressed before reconnect, auto-start listening
                if self._listen_after_connect:
                    self._listen_after_connect = False
                    self._keep_listening = True
                    await self._start_listening()
                return
            except Exception as e:
                log.warning("connect failed (%d/%d): %s, retrying in %ds", attempt, max_retries, e, retry_delay)
                self._update_display(
                    status="Disconnected", emoji="❌",
                    text=f"Retry {attempt}/{max_retries} in {retry_delay}s...\n{e}"
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)

        if self._running:
            log.error("connect failed after %d attempts", max_retries)
            self._set_state(self.IDLE)
            self._update_display(status="Disconnected", emoji="❌", text="Press button to retry...")

    async def _on_goodbye(self):
        """Server sent goodbye — session ended gracefully. Don't reconnect."""
        log.info("goodbye received, ending conversation")
        self._keep_listening = False
        self.recorder.stop()
        await self.player.stop()
        self._set_state(self.IDLE)
        self._update_display(status="Connected", emoji="😄", text="Press button to wake...")

    async def _on_disconnected(self):
        """Handle server disconnection."""
        if not self._running or self._reconnecting:
            return
        # If server ended session (goodbye or graceful close), just go idle
        if self.client and getattr(self.client, '_goodbye_received', False):
            log.info("session ended gracefully, not reconnecting")
            self._set_state(self.IDLE)
            self._update_display(status="Idle", emoji="\U0001f604", text="Press button to wake...")
            return
        # Only auto-reconnect if user was actively interacting
        if self._state in (self.LISTENING, self.SPEAKING):
            await self._reconnect()
        else:
            # Idle disconnect (server timeout) — wait for button press
            log.info("idle disconnect, waiting for button press to reconnect")
            self._set_state(self.IDLE)
            self._update_display(status="Idle", emoji="\U0001f604", text="Press button to wake...")

    async def _reconnect(self):
        """Reconnect to server (called from disconnect handler or button press)."""
        if self._reconnecting:
            return
        self._reconnecting = True
        self._keep_listening = False
        try:
            log.warning("disconnected from server, reconnecting...")
            self.recorder.stop()
            await self.player.stop()
            self._set_state(self.IDLE)
            self._update_display(status="Reconnecting...", emoji="🔄", text="")
            await asyncio.sleep(2)  # Brief delay before reconnecting
            await self._activate_and_connect()
        except Exception as e:
            log.error("reconnect failed: %s", e)
            self._update_display(status="Disconnected", emoji="❌", text=str(e))
        finally:
            self._reconnecting = False

    # ==================== Button Events ====================
    def _on_button_press(self):
        """Button pressed — wake: start auto-listen or abort TTS."""
        if self._loop:
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future, self._handle_button_press()
            )

    def _on_button_release(self):
        """Button released — no-op in push-to-wake mode."""
        pass

    async def _handle_button_press(self):
        # If disconnected, trigger reconnect and auto-listen after
        if not self.client or not self.client.connected:
            if not self._reconnecting:
                self._listen_after_connect = True
                asyncio.create_task(self._reconnect())
            return

        if self._state == self.SPEAKING:
            # Abort current TTS and start new listen
            self._keep_listening = False
            await self.client.send_abort()
            await self.player.stop()

        if self._state in (self.IDLE, self.SPEAKING):
            self._keep_listening = True  # Enable auto-listen cycle
            await self._start_listening()

    # ==================== Listening ====================
    async def _start_listening(self):
        """Enter auto-listen mode. Server VAD controls when listening ends."""
        if not self.client or not self.client.connected:
            self._keep_listening = False
            self._set_state(self.IDLE)
            return
        self._set_state(self.LISTENING)
        self._update_display(status="Listening...", emoji="🎤")
        self._tts_text_buffer = ""

        try:
            await self.client.send_listen_start(mode="auto")
        except Exception as e:
            log.warning("send listen_start failed: %s", e)
            self._keep_listening = False
            self._set_state(self.IDLE)
            self._update_display(status="Idle", emoji="😄", text="Press button to wake...")
            # Server closed connection after TTS — treat as graceful goodbye
            if self.client:
                self.client._goodbye_received = True
            return
        self.recorder.start()
        self._recording_task = asyncio.create_task(self._stream_audio())

    async def _stop_listening(self):
        """Stop recording and notify server."""
        self.recorder.stop()
        if self._recording_task:
            self._recording_task.cancel()
            try:
                await self._recording_task
            except asyncio.CancelledError:
                pass
            self._recording_task = None
        await self.client.send_listen_stop()

    async def _stream_audio(self):
        """Record PCM, encode to Opus, and send to server."""
        try:
            async for pcm_frame in self.recorder.read_frames(self.encoder.frame_bytes):
                if self._state != self.LISTENING:
                    break
                opus_data = self.encoder.encode(pcm_frame)
                await self.client.send_audio(opus_data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("audio streaming error: %s", e)

    # ==================== Server Event Callbacks ====================
    async def _on_listen_stop(self):
        """Server-side VAD detected end of speech; stop recording."""
        if self._state == self.LISTENING:
            await self._stop_listening()
            self._update_display(status="Thinking...", emoji="\U0001f914")
        elif self._state == self.IDLE:
            # Server sent listen stop while idle — end of conversation
            self._keep_listening = False

    async def _on_stt(self, text: str):
        """Received ASR result from server."""
        log.info("STT: %s", text)
        # Server recognized speech; stop recording (server VAD triggered)
        if self._state == self.LISTENING:
            await self._stop_listening()
            self._update_display(status="Thinking...", emoji="🤔")
        self._update_display(text=f"🗣️ {text}")

    async def _on_llm_emotion(self, emoji: str):
        """Received emotion/emoji from LLM."""
        self._update_display(emoji=emoji)

    async def _on_tts_start(self):
        """TTS playback starting."""
        self._set_state(self.SPEAKING)
        self._update_display(status="Speaking...")
        self.player.start()

    async def _on_tts_audio(self, opus_data: bytes):
        """Received Opus TTS audio frame."""
        try:
            pcm = self.decoder.decode(opus_data)
            await self.player.put(pcm)
        except Exception as e:
            log.error("decode error: %s", e)

    async def _on_tts_sentence_start(self, text: str):
        """New TTS sentence starting."""
        self._tts_text_buffer += text
        self._update_display(text=self._tts_text_buffer)

    async def _on_tts_stop(self):
        """TTS playback finished. Auto-restart listening if in conversation."""
        await self.player.stop()
        if self._keep_listening and self.client and self.client.connected:
            await self._start_listening()
        else:
            self._set_state(self.IDLE)
            self._update_display(status="Connected", emoji="😄")

    async def _on_mcp(self, payload: dict):
        """Handle MCP tool call from server.

        The device acts as MCP SERVER; the gateway is the MCP CLIENT.
        We only respond to requests (with an id).  We must NOT send
        notifications/initialized back — that notification flows from
        client→server in MCP, and the gateway already sends it to us.
        Sending it back causes the gateway to forward it to
        parseOtherMessage where it triggers a spurious goodbye (no
        matching pending request, bridge is null).
        """
        result = await self.mcp.handle(payload)
        if result:
            mcp_id, response = result
            await self.client.send_mcp_response(mcp_id, response)

            # Signal that MCP handshake is complete after tools/list response.
            # This unblocks the MQTT client's hello message — the gateway
            # needs our tool cache populated before we trigger bridge creation.
            rpc = payload.get("payload", {})
            method = rpc.get("method", "")
            if method == "tools/list" and hasattr(self.client, "mark_mcp_complete"):
                self.client.mark_mcp_complete()

    async def _on_iot(self, commands: list):
        """Handle IoT commands from server."""
        log.info("IoT commands: %s", commands)
        # IoT Thing handling to be implemented in iot/ module

    # ==================== Display Helpers ====================
    def _update_display(self, **kwargs):
        if self.display:
            self.display.update(**kwargs)

    async def _battery_display_loop(self):
        """Periodically update battery display."""
        while self._running:
            if self.battery.level >= 0:
                self._update_display(
                    battery_level=self.battery.level,
                    battery_color=self.battery.get_color(),
                )
            await asyncio.sleep(config.BATTERY_POLL_INTERVAL)

    # ==================== Wake Word Trigger ====================
    async def trigger_wake_word(self):
        """Called when wake word is detected. Same as button press."""
        if not self.client or not self.client.connected:
            return
        if self._state == self.SPEAKING:
            await self.client.send_abort()
            await self.player.stop()
        if self._state in (self.IDLE, self.SPEAKING):
            self._keep_listening = True
            await self._start_listening()
