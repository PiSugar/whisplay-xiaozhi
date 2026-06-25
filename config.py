import os
from dotenv import load_dotenv

load_dotenv()

# Application version
APP_VERSION = "1.0.0"


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


def _csv(key: str, default: str = "") -> str:
    return ",".join(item.strip() for item in _get(key, default).split(",") if item.strip())


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
AUDIO_OUTPUT_TAIL_PADDING_MS = _int("AUDIO_OUTPUT_TAIL_PADDING_MS", "120")
AUDIO_OUTPUT_DRAIN_TIMEOUT_SEC = _float("AUDIO_OUTPUT_DRAIN_TIMEOUT_SEC", "4")

# Wake word
WAKE_WORD_ENABLED = _bool("WAKE_WORD_ENABLED", "false")
WAKE_WORDS = [w.strip() for w in _get("WAKE_WORDS", "hey_jarvis").split(",") if w.strip()]
WAKE_WORD_THRESHOLD = _float("WAKE_WORD_THRESHOLD", "0.5")
WAKE_WORD_COOLDOWN_SEC = _float("WAKE_WORD_COOLDOWN_SEC", "1.5")

# Display
LCD_BRIGHTNESS = _int("LCD_BRIGHTNESS", "100")
FONT_PATH = _get("FONT_PATH")
DISPLAY_SCROLL_SPEED = _float("DISPLAY_SCROLL_SPEED", "1.0")

# Battery
PISUGAR_ENABLED = _bool("PISUGAR_ENABLED", "true")
PISUGAR_HOST = _get("PISUGAR_HOST", "127.0.0.1")
PISUGAR_PORT = _int("PISUGAR_PORT", "8423")
BATTERY_POLL_INTERVAL = _int("BATTERY_POLL_INTERVAL", "5")

# MCP local command tool
LOCAL_COMMAND_TOOL_ENABLED = _bool("XIAOZHI_LOCAL_COMMAND_TOOL_ENABLED", "true")
LOCAL_COMMAND_ALLOWLIST = _csv(
    "XIAOZHI_LOCAL_COMMAND_ALLOWLIST",
    "date,uptime,hostname,whoami,df,free,ip,iwgetid,vcgencmd",
)
LOCAL_COMMAND_UNSAFE = _bool("XIAOZHI_LOCAL_COMMAND_UNSAFE", "false")
LOCAL_COMMAND_USE_SHELL = _bool("XIAOZHI_LOCAL_COMMAND_USE_SHELL", "false")
LOCAL_COMMAND_TIMEOUT_SEC = _float("XIAOZHI_LOCAL_COMMAND_TIMEOUT_SEC", "5")
LOCAL_COMMAND_OUTPUT_LIMIT = _int("XIAOZHI_LOCAL_COMMAND_OUTPUT_LIMIT", "4000")

# MCP web tools
WEB_TOOLS_ENABLED = _bool("XIAOZHI_WEB_TOOLS_ENABLED", "true")
WEB_TOOL_PROXY = _get("XIAOZHI_WEB_TOOL_PROXY")
WEB_TOOL_TIMEOUT_SEC = _float("XIAOZHI_WEB_TOOL_TIMEOUT_SEC", "15")
WEB_TOOL_TEXT_LIMIT = _int("XIAOZHI_WEB_TOOL_TEXT_LIMIT", "6000")
WEB_TOOL_LINK_LIMIT = _int("XIAOZHI_WEB_TOOL_LINK_LIMIT", "30")
WEB_SEARCH_RESULT_LIMIT = _int("XIAOZHI_WEB_SEARCH_RESULT_LIMIT", _get("XIAOZHI_GOOGLE_SEARCH_RESULT_LIMIT", "5"))
GOOGLE_SEARCH_API_KEY = _get("XIAOZHI_GOOGLE_SEARCH_API_KEY")
GOOGLE_SEARCH_ENGINE_ID = _get("XIAOZHI_GOOGLE_SEARCH_ENGINE_ID")
