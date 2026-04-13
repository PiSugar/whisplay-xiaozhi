"""
XiaoZhi OTA client — handles device activation and pairing.

Flow:
1. POST to OTA URL with device info → server returns activation code
2. Device displays code on screen, user enters it on xiaozhi.me
3. Device polls Activate endpoint until binding succeeds
4. On success, re-check version → server returns websocket config (url + token)
5. Credentials are persisted locally for future connections

Reference: xiaozhi-esp32 ota.cc
"""

import hashlib
import hmac as hmac_mod
import json
import logging
import os
import platform
import socket
import uuid
from pathlib import Path

import requests

log = logging.getLogger("ota")

# Persistent credential storage
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CRED_FILE = _DATA_DIR / "credentials.json"
_EFUSE_FILE = _DATA_DIR / "efuse.json"


def _load_or_create_efuse() -> dict:
    """Load efuse.json, creating it if it doesn't exist."""
    if _EFUSE_FILE.exists():
        try:
            data = json.loads(_EFUSE_FILE.read_text())
            if data.get("serial_number") and data.get("hmac_key"):
                return data
        except Exception as e:
            log.warning("failed to load efuse.json: %s", e)

    # Generate fresh efuse data
    mac = _get_mac().lower().replace(":", "")
    short_hash = hashlib.md5(mac.encode()).hexdigest()[:8].upper()
    serial_number = f"SN-{short_hash}-{mac}"

    # Generate hmac_key from hardware identifiers
    identifiers = []
    hostname = platform.node()
    if hostname:
        identifiers.append(hostname)
    mac_addr = _get_mac().lower()
    if mac_addr:
        identifiers.append(mac_addr)
    fingerprint_str = "||".join(identifiers) if identifiers else platform.system()
    hmac_key = hashlib.sha256(fingerprint_str.encode("utf-8")).hexdigest()

    efuse_data = {
        "mac_address": _get_mac().lower(),
        "serial_number": serial_number,
        "hmac_key": hmac_key,
        "activation_status": False,
        "device_fingerprint": {
            "system": platform.system(),
            "hostname": hostname,
            "mac_address": _get_mac().lower(),
        },
    }

    _EFUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _EFUSE_FILE.write_text(json.dumps(efuse_data, indent=2, ensure_ascii=False))
    log.info("created efuse.json: serial=%s", serial_number)
    return efuse_data


def _get_mac() -> str:
    """Get device MAC address (with colons, uppercase) matching ESP32 format."""
    for iface in ("wlan0", "eth0"):
        try:
            with open(f"/sys/class/net/{iface}/address") as f:
                return f.read().strip().upper()
        except Exception:
            pass
    # Fallback: generate a random MAC-like identifier
    h = uuid.uuid4().hex[:12].upper()
    return ":".join(h[i:i+2] for i in range(0, 12, 2))


# Match py-xiaozhi identifiers so the server recognises us
_BOARD_TYPE = "bread-compact-wifi"
_APP_NAME = "py-xiaozhi"
_APP_VERSION = "2.0.0"


def _get_local_ip() -> str:
    """Get device local IP address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _get_user_agent() -> str:
    """Build a user-agent string matching py-xiaozhi format."""
    return f"{_BOARD_TYPE}/{_APP_NAME}-{_APP_VERSION}"


class OtaClient:
    """Handles XiaoZhi OTA check-version and device activation."""

    def __init__(self, ota_url: str, device_id: str = "", client_id: str = ""):
        self.ota_url = ota_url.rstrip("/") + "/"  # Ensure trailing slash
        self.device_id = device_id or _get_mac()
        self.client_id = client_id or self._load_or_create_client_id()
        self.efuse = _load_or_create_efuse()

        # Activation state
        self.activation_code: str | None = None
        self.activation_message: str | None = None
        self.activation_challenge: str | None = None
        self.activation_timeout_ms: int = 30000

        # Websocket config (populated after successful pairing)
        self.ws_url: str | None = None
        self.ws_token: str | None = None

        # MQTT config (populated after successful pairing)
        self.mqtt_endpoint: str | None = None
        self.mqtt_client_id: str | None = None
        self.mqtt_username: str | None = None
        self.mqtt_password: str | None = None
        self.mqtt_publish_topic: str | None = None
        self.mqtt_subscribe_topic: str | None = None

    # ==================== Credential persistence ====================

    @staticmethod
    def load_credentials() -> dict | None:
        """Load saved credentials from disk. Returns dict or None."""
        if _CRED_FILE.exists():
            try:
                data = json.loads(_CRED_FILE.read_text())
                # Valid if we have either MQTT or WebSocket credentials
                if data.get("mqtt_endpoint") or (data.get("ws_url") and data.get("ws_token")):
                    return data
            except Exception as e:
                log.warning("failed to load credentials: %s", e)
        return None

    def save_credentials(self):
        """Persist connection credentials to disk."""
        _CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
        creds = {
            "ws_url": self.ws_url,
            "ws_token": self.ws_token,
            "device_id": self.device_id,
            "client_id": self.client_id,
            "mqtt_endpoint": self.mqtt_endpoint,
            "mqtt_client_id": self.mqtt_client_id,
            "mqtt_username": self.mqtt_username,
            "mqtt_password": self.mqtt_password,
            "mqtt_publish_topic": self.mqtt_publish_topic,
            "mqtt_subscribe_topic": self.mqtt_subscribe_topic,
        }
        _CRED_FILE.write_text(json.dumps(creds, indent=2))
        log.info("credentials saved to %s", _CRED_FILE)

    def _load_or_create_client_id(self) -> str:
        """Load existing client_id from credentials or generate a new one."""
        creds = self.load_credentials()
        if creds and creds.get("client_id"):
            return creds["client_id"]
        return str(uuid.uuid4())

    # ==================== HTTP helpers ====================

    def _headers(self) -> dict:
        return {
            "Device-Id": self.device_id,
            "Client-Id": self.client_id,
            "Content-Type": "application/json",
            "User-Agent": _get_user_agent(),
            "Accept-Language": "zh-CN",
            "Activation-Version": _APP_VERSION,
        }

    # ==================== Check Version ====================

    def check_version(self) -> bool:
        """
        POST to OTA URL to check activation status and get websocket config.

        Returns True if websocket config is available (device is paired).
        Returns False if activation is required (code will be set).
        Raises on network/server error.
        """
        url = self.ota_url
        headers = self._headers()

        # Match py-xiaozhi payload format exactly
        hmac_key = self.efuse.get("hmac_key", "unknown")
        body = {
            "application": {
                "version": _APP_VERSION,
                "elf_sha256": hmac_key,
            },
            "board": {
                "type": _BOARD_TYPE,
                "name": _APP_NAME,
                "ip": _get_local_ip(),
                "mac": self.device_id,
            },
        }

        log.info("checking version at %s", url)
        resp = requests.post(url, headers=headers, json=body, timeout=15)
        resp.raise_for_status()

        data = resp.json()
        log.info("check_version response: %s", json.dumps(data, ensure_ascii=False)[:500])

        # Parse activation section
        self.activation_code = None
        self.activation_message = None
        self.activation_challenge = None

        activation = data.get("activation")
        if isinstance(activation, dict):
            self.activation_code = activation.get("code")
            self.activation_message = activation.get("message")
            self.activation_challenge = activation.get("challenge")
            timeout = activation.get("timeout_ms")
            if isinstance(timeout, (int, float)):
                self.activation_timeout_ms = int(timeout)

        # Parse websocket section
        self.ws_url = None
        self.ws_token = None

        websocket = data.get("websocket")
        if isinstance(websocket, dict):
            self.ws_url = websocket.get("url")
            self.ws_token = websocket.get("token")

        # Parse MQTT section
        self.mqtt_endpoint = None
        self.mqtt_client_id = None
        self.mqtt_username = None
        self.mqtt_password = None
        self.mqtt_publish_topic = None
        self.mqtt_subscribe_topic = None

        mqtt_info = data.get("mqtt")
        if isinstance(mqtt_info, dict):
            self.mqtt_endpoint = mqtt_info.get("endpoint")
            self.mqtt_client_id = mqtt_info.get("client_id")
            self.mqtt_username = mqtt_info.get("username")
            self.mqtt_password = mqtt_info.get("password")
            self.mqtt_publish_topic = mqtt_info.get("publish_topic")
            self.mqtt_subscribe_topic = mqtt_info.get("subscribe_topic")

        # If activation is required, don't treat as paired even if config is present.
        if self.activation_code or self.activation_challenge:
            return False

        # Paired if we have WebSocket config or MQTT config
        has_ws = bool(self.ws_url and self.ws_token)
        has_mqtt = bool(self.mqtt_endpoint and self.mqtt_username and self.mqtt_password)

        if has_mqtt or has_ws:
            self.save_credentials()
            return True

        return False

    # ==================== Activate ====================

    def activate(self) -> str:
        """
        POST to activate endpoint to poll activation status.
        Uses v2 activation with HMAC-SHA256 signed payload.

        Returns:
            "ok"      — activation complete
            "pending" — user hasn't entered code yet, keep polling
            "error"   — activation failed
        """
        url = f"{self.ota_url}activate"
        headers = self._headers()

        # Build v2 activation payload with HMAC-SHA256
        serial_number = self.efuse.get("serial_number", "")
        hmac_key = self.efuse.get("hmac_key", "")
        challenge = self.activation_challenge or ""

        # Compute HMAC-SHA256 signature
        signature = hmac_mod.new(
            hmac_key.encode(), challenge.encode(), hashlib.sha256
        ).hexdigest()

        payload = {
            "Payload": {
                "algorithm": "hmac-sha256",
                "serial_number": serial_number,
                "challenge": challenge,
                "hmac": signature,
            }
        }

        log.info("activating at %s (v2, serial=%s)", url, serial_number)
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
        except Exception as e:
            log.error("activate request failed: %s", e)
            return "error"

        if resp.status_code == 200:
            log.info("activation successful")
            # Mark activation in efuse
            self.efuse["activation_status"] = True
            try:
                _EFUSE_FILE.write_text(json.dumps(self.efuse, indent=2, ensure_ascii=False))
            except Exception:
                pass
            return "ok"
        elif resp.status_code == 202:
            log.debug("activation pending (202)")
            return "pending"
        else:
            log.warning("activate returned %d: %s", resp.status_code, resp.text[:200])
            return "error"
