# Whisplay XiaoZhi

[中文版](README_CN.md)

<img width="200" alt="68747470733a2f2f646f63732e706973756761722e636f6d2f696d672f77686973706c61795f6c6f676f4034782d382e706e67" src="https://github.com/user-attachments/assets/b168a14c-71d4-473d-9fd9-196802bfc5e9" />

XiaoZhi AI voice client for Raspberry Pi + Whisplay HAT + PiSugar battery.

Connects to the [XiaoZhi AI platform](https://xiaozhi.me) via WebSocket, providing a complete voice interaction pipeline: ASR (speech recognition), LLM (language model), and TTS (text-to-speech) — all in a pocket-sized device.

## Features

- **WebSocket Voice Conversation** — XiaoZhi protocol v1 with Opus audio codec
- **Auto Pairing** — Device shows a verification code on LCD; enter it on xiaozhi.me to bind (no token needed)
- **Push-to-Wake** — Button press wakes the device and starts auto-listening (server-side VAD controls when speech ends)
- **LCD Display** — 240×280 ST7789V showing status, emoji, scrolling text, Wi-Fi signal, and battery level
- **RGB LED** — Automatic color changes based on state (idle / listening / thinking / speaking / error)
- **Battery Monitor** — Real-time PiSugar battery level display
- **whisplay-daemon Ready** — Auto-adapts to daemon framebuffer / button / LED mode when available
- **Wake Word** — Hands-free activation via openwakeword
- **MCP Support** — Server-side tool invocation (JSON-RPC 2.0)

## Hardware Requirements

| Component | Description |
|-----------|-------------|
| Raspberry Pi | Zero 2W / Pi 4 / Pi 5 |
| Whisplay HAT | PiSugar Whisplay HAT (LCD + mic + speaker + RGB LED + button) |
| PiSugar Battery | 1200mAh / 5000mAh |
| WM8960 | Audio codec (built into HAT) |

## Quick Start

### 1. Install

```bash
git clone https://github.com/PiSugar/whisplay-xiaozhi.git
cd whisplay-xiaozhi
bash install.sh
```

### 2. Configure

Copy the template and customize if needed:

```bash
cp .env.template .env
```

Most settings work out of the box. The device will auto-detect its MAC address and pair with the server.

If the system provides `whisplay-daemon`, register this project as a daemon app entry (`app_id: whisplay-xiaozhi`) and launch it from daemon app management.

### 3. Run

```bash
bash run.sh
```

### 4. First-time Pairing

On first boot, the LCD will display a **verification code** (e.g., `123456`).

1. Go to [xiaozhi.me](https://xiaozhi.me) and sign in
2. Add a new device and enter the verification code shown on the LCD
3. Once bound, the device automatically connects and is ready to use

Credentials are saved locally — subsequent boots skip the pairing step.

### 5. Usage

- **Press button** → Device wakes up and starts listening (auto-stop via server VAD)
- **Press button during response** → Interrupts current response and starts a new conversation
- **Wake word** → Same as button press (if enabled)

## Project Structure

```
whisplay-xiaozhi/
├── main.py                 # Entry point
├── config.py               # Configuration (.env reader)
├── application.py          # Main state machine
├── protocol/
│   ├── websocket_client.py # XiaoZhi WebSocket protocol client
│   └── mcp_handler.py      # MCP tool call handler
├── audio/
│   ├── audio_codec.py      # Opus encode/decode
│   ├── audio_recorder.py   # Microphone recording (sox)
│   └── audio_player.py     # Speaker playback (sox)
├── hardware/
│   ├── whisplay_board.py   # Whisplay HAT hardware abstraction
│   ├── battery.py          # PiSugar battery monitor
│   └── led_controller.py   # RGB LED controller
├── display/
│   ├── ui_renderer.py      # LCD UI rendering (30 FPS)
│   └── text_utils.py       # Text/emoji rendering utilities
├── wakeword/
│   └── detector.py         # Wake word detection
├── iot/
│   ├── thing.py            # IoT thing base class
│   └── thing_manager.py    # IoT thing registry
├── assets/
│   ├── emoji_svg/          # Emoji SVG icons
│   └── logo.png            # Startup logo
├── service/
│   └── whisplay-xiaozhi@.service  # systemd service
├── requirements.txt
├── install.sh
├── run.sh
├── .env.template
└── README.md
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `XIAOZHI_OTA_URL` | OTA / activation API URL | `https://api.tenclass.net/xiaozhi/ota/` |
| `XIAOZHI_DEVICE_ID` | Device ID (auto-detect MAC) | — |
| `ALSA_INPUT_DEVICE` | ALSA recording device | `default` |
| `ALSA_OUTPUT_DEVICE` | ALSA playback device | `default` |
| `WAKE_WORD_ENABLED` | Enable wake word | `false` |
| `WAKE_WORDS` | Wake words (comma-separated) | `hey_jarvis` |
| `LCD_BRIGHTNESS` | LCD brightness (0-100) | `100` |
| `DISPLAY_SCROLL_SPEED` | Text scroll pixels per rendered frame | `1.0` |
| `PISUGAR_ENABLED` | Enable battery monitor | `true` |
| `XIAOZHI_LOCAL_COMMAND_TOOL_ENABLED` | Expose the `local_command` MCP tool | `true` |
| `XIAOZHI_LOCAL_COMMAND_ALLOWLIST` | Comma-separated executable names allowed by `local_command` | `date,uptime,hostname,whoami,df,free,ip,iwgetid,vcgencmd` |
| `XIAOZHI_LOCAL_COMMAND_UNSAFE` | Allow any local executable; use only on trusted devices | `false` |
| `XIAOZHI_LOCAL_COMMAND_USE_SHELL` | Enable shell syntax for `local_command`; requires unsafe mode | `false` |
| `XIAOZHI_LOCAL_COMMAND_TIMEOUT_SEC` | Max seconds per local command | `5` |
| `XIAOZHI_LOCAL_COMMAND_OUTPUT_LIMIT` | Max stdout/stderr characters returned | `4000` |
| `XIAOZHI_WEB_TOOLS_ENABLED` | Expose `fetch_webpage` and `web_search` MCP tools | `true` |
| `XIAOZHI_WEB_TOOL_PROXY` | Optional proxy URL for web tools; falls back to `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY` | — |
| `XIAOZHI_WEB_TOOL_TIMEOUT_SEC` | HTTP timeout for web tools | `15` |
| `XIAOZHI_WEB_TOOL_TEXT_LIMIT` | Max webpage text characters returned | `6000` |
| `XIAOZHI_WEB_TOOL_LINK_LIMIT` | Max links returned per fetched webpage | `30` |
| `XIAOZHI_WEB_SEARCH_RESULT_LIMIT` | Max web search results returned | `5` |
| `XIAOZHI_GOOGLE_SEARCH_API_KEY` | Google Programmable Search JSON API key for `search_type=sites` | — |
| `XIAOZHI_GOOGLE_SEARCH_ENGINE_ID` | Google Programmable Search Engine ID (`cx`) for `search_type=sites` | — |

## MCP Tools

When MCP is enabled by the XiaoZhi gateway, the device advertises a `local_command`
tool. It accepts a `command` string and optional `timeout`, runs the command
locally without a shell, and returns `stdout`, `stderr`, and `exit_code`.
By default only the executables in `XIAOZHI_LOCAL_COMMAND_ALLOWLIST` can run.
Set `XIAOZHI_LOCAL_COMMAND_UNSAFE=true` only for fully trusted deployments.
Set `XIAOZHI_LOCAL_COMMAND_USE_SHELL=true` as well if commands need shell
features such as pipes, redirects, `&&`, or sudo password piping.

When `XIAOZHI_WEB_TOOLS_ENABLED=true`, the device also advertises:

- `fetch_webpage`: fetches an HTTP(S) URL and returns the page title, readable text, and links. It can also open a link from the current or previous page using `link_text` or `link_index`.
- `web_search`: searches the web and returns compact result titles and URLs. `search_type=web` uses DuckDuckGo HTML, `search_type=news` uses Google News RSS, and `search_type=sites` uses Google Programmable Search JSON API when configured.

Set `XIAOZHI_WEB_TOOL_PROXY` to route those web requests through a proxy, or leave it
empty to use standard proxy environment variables if they are already set.

## Auto-Start on Boot

```bash
# Install systemd service (replace 'pi' with your username)
sudo cp service/whisplay-xiaozhi@.service /etc/systemd/system/
sudo systemctl enable whisplay-xiaozhi@pi
sudo systemctl start whisplay-xiaozhi@pi

# View logs
sudo journalctl -u whisplay-xiaozhi@pi -f
```

If `whisplay-daemon` is already running, use the daemon-registered `whisplay-xiaozhi` app entry instead of installing the standalone service with `startup.sh`.

## Protocol Reference

This project implements the XiaoZhi ESP32 WebSocket protocol v1:
- [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)
- [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi)
- [OTA activation](https://my.feishu.cn/wiki/FjW6wZmisimNBBkov6OcmfvknVd) Device registers via HTTP, user binds with verification code
- [WebSocket](https://my.feishu.cn/wiki/M0XiwldO9iJwHikpXD5cEx71nKh) Hello handshake + Opus audio streaming + JSON control messages

## License

GPL-3.0
