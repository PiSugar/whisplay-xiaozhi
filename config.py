import os
from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _bool(key: str, default: str = "false") -> bool:
    return _get(key, default).lower() in ("true", "1", "yes")


def _int(key: str, default: str = "0") -> int:
    try:
        return int(_get(key, default))
    except ValueError:
        return int(default)


def _float(key: str, default: str = "0.0") -> float:
    try:
        return float(_get(key, default))
    except ValueError:
        return float(default)


# XiaoZhi server
OTA_URL = _get("XIAOZHI_OTA_URL", "https://api.tenclass.net/xiaozhi/ota/")
DEVICE_ID = _get("XIAOZHI_DEVICE_ID")
CLIENT_ID = _get("XIAOZHI_CLIENT_ID")

# Direct WebSocket connection (bypasses OTA activation entirely)
# Set both to connect directly to a self-hosted xiaozhi-esp32-server.
WS_URL = _get("XIAOZHI_WS_URL")    # e.g., ws://192.168.1.100:8000/xiaozhi/v1/
WS_TOKEN = _get("XIAOZHI_WS_TOKEN")  # auth token (empty string if auth disabled)

# Audio
ALSA_INPUT_DEVICE = _get("ALSA_INPUT_DEVICE", "default")
ALSA_OUTPUT_DEVICE = _get("ALSA_OUTPUT_DEVICE", "default")
AUDIO_INPUT_SAMPLE_RATE = _int("AUDIO_INPUT_SAMPLE_RATE", "16000")
AUDIO_OUTPUT_SAMPLE_RATE = _int("AUDIO_OUTPUT_SAMPLE_RATE", "24000")

# Wake word
WAKE_WORD_ENABLED = _bool("WAKE_WORD_ENABLED", "false")
WAKE_WORDS = [w.strip() for w in _get("WAKE_WORDS", "hey_jarvis").split(",") if w.strip()]
WAKE_WORD_THRESHOLD = _float("WAKE_WORD_THRESHOLD", "0.5")
WAKE_WORD_COOLDOWN_SEC = _float("WAKE_WORD_COOLDOWN_SEC", "1.5")

# Display
LCD_BRIGHTNESS = _int("LCD_BRIGHTNESS", "100")
FONT_PATH = _get("FONT_PATH")

# Battery
PISUGAR_ENABLED = _bool("PISUGAR_ENABLED", "true")
PISUGAR_HOST = _get("PISUGAR_HOST", "127.0.0.1")
PISUGAR_PORT = _int("PISUGAR_PORT", "8423")
BATTERY_POLL_INTERVAL = _int("BATTERY_POLL_INTERVAL", "5")
