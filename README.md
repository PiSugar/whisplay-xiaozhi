# Whisplay XiaoZhi

[中文版](README_CN.md)

<img width="200" alt="68747470733a2f2f646f63732e706973756761722e636f6d2f696d672f77686973706c61795f6c6f676f4034782d382e706e67" src="https://github.com/user-attachments/assets/b168a14c-71d4-473d-9fd9-196802bfc5e9" />

XiaoZhi AI voice client for Raspberry Pi + Whisplay HAT + PiSugar battery.

Connects to the [XiaoZhi AI platform](https://xiaozhi.me) via WebSocket, providing a complete voice interaction pipeline: ASR (speech recognition), LLM (language model), and TTS (text-to-speech) — all in a pocket-sized device.

## Features

- **WebSocket Voice Conversation** — XiaoZhi protocol v1 with Opus audio codec
- **Auto Pairing** — Device shows a verification code on LCD; enter it on xiaozhi.me to bind (no token needed)
- **Push-to-Wake** — Button press wakes the device and starts auto-listening (server-side VAD controls when speech ends)
- **Optional Voice Barge-In** — sustained speech can interrupt TTS and immediately start a new turn
- **Configurable LCD UI** — classic status/emoji view or the audio-reactive watercolor orb ported from `whisplay-chatgpt`
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

Set `DISPLAY_UI_STYLE=watercolor` in `.env` and restart the app to enable the
watercolor orb. Listening expands the orb with microphone energy; assistant
speech animates its internal pigment flow.

Set `BARGE_IN_ENABLED=true` to allow sustained speech to interrupt assistant
playback. If speaker echo causes false triggers, raise `BARGE_IN_MIN_RMS`.

### Rust watercolor renderer

Watercolor mode always uses the Rust renderer and fails clearly when a
compatible extension is absent. The repository includes the Linux AArch64
extension built on a CM5 and validated on a Zero 2 W at
`rust/watercolor_renderer/prebuilt/linux-aarch64/_watercolor_rust.so`. The app
loads the deployed copy under `display/` first, then this archived build.
The native path mirrors ChatGPT's three logarithmic audio bands, independent
cumulative pigment phases, linear-burn colour layers, and watercolor texture.

To rebuild it, use an AArch64 machine with Rust installed (a CM5 is suitable):

```bash
bash tools/build_watercolor_rust.sh
```

The script archives the output under `prebuilt/linux-aarch64` and also copies it
to `display/_watercolor_rust.so` for the current environment. For a Zero 2 W,
the recommended setting is `WATERCOLOR_THREADS=2`; four threads maximize FPS
but leave less CPU headroom for audio and networking.
On 32-bit Raspberry Pi OS the same script builds the identical Rust renderer
natively and archives it under `prebuilt/linux-armv7l` or `prebuilt/linux-armv6l`.
The installer selects the matching architecture automatically; Python is used
only for caption layout and never for orb pixel rendering.

| Variable | Description | Default |
|----------|-------------|---------|
| `XIAOZHI_OTA_URL` | OTA / activation API URL | `https://api.tenclass.net/xiaozhi/ota/` |
| `XIAOZHI_DEVICE_ID` | Device ID (auto-detect MAC) | — |
| `ALSA_INPUT_DEVICE` | ALSA recording device | `default` |
| `ALSA_OUTPUT_DEVICE` | ALSA playback device | `default` |
| `BARGE_IN_ENABLED` | Allow voice to interrupt assistant TTS | `false` |
| `BARGE_IN_MIN_RMS` | Minimum raw PCM RMS needed for interruption | `850` |
| `BARGE_IN_REQUIRED_FRAMES` | Consecutive 60 ms speech frames required | `4` |
| `BARGE_IN_WARMUP_MS` | Initial echo-learning period for each reply | `350` |
| `WAKE_WORD_ENABLED` | Enable wake word | `false` |
| `WAKE_WORDS` | Wake words (comma-separated) | `hey_jarvis` |
| `LCD_BRIGHTNESS` | LCD brightness (0-100) | `100` |
| `DISPLAY_SCROLL_SPEED` | Text scroll pixels per rendered frame | `1.0` |
| `DISPLAY_UI_STYLE` | LCD UI: `classic` or `watercolor` | `classic` |
| `WATERCOLOR_FPS` | Watercolor animation FPS (1-20) | `8` |
| `WATERCOLOR_DIAMETER` | Orb diameter in pixels (100-220) | `168` |
| `WATERCOLOR_RENDER_SCALE` | Internal render scale; lower is faster (0.2-1.0) | `0.37` |
| `WATERCOLOR_SMOOTH_FBM` | Enable smoother FBM sampling on faster boards | `false` |
| `WATERCOLOR_TEMPORAL_3D` | Enable higher-quality temporal noise | `false` |
| `WATERCOLOR_AUDIO_REACTIVITY` | Assistant audio deformation gain (0.5-5.0) | `3.6` |
| `WATERCOLOR_SPEECH_MOTION` | Assistant pigment travel gain (0.5-5.0) | `4.5` |
| `WATERCOLOR_THREADS` | Native renderer worker threads (1-4) | `2` |
| `WATERCOLOR_CAPTION_PAGE_SECONDS` | Minimum seconds to show each caption page | `3.0` |
| `WATERCOLOR_CAPTION_FONT_SIZE` | Watercolor caption font size in pixels (10-24) | `15` |
| `WATERCOLOR_CAPTION_OFFSET_X` | Horizontal caption offset in pixels (-20 to 20) | `3` |
| `PISUGAR_ENABLED` | Enable battery monitor | `true` |
| `XIAOZHI_LOCAL_COMMAND_TOOL_ENABLED` | Expose the `local_command` MCP tool | `true` |
| `XIAOZHI_LOCAL_COMMAND_ALLOWLIST` | Comma-separated executable names allowed by `local_command` | `date,uptime,hostname,whoami,df,free,ip,iwgetid,vcgencmd` |
| `XIAOZHI_LOCAL_COMMAND_UNSAFE` | Allow any local executable; use only on trusted devices | `false` |
| `XIAOZHI_LOCAL_COMMAND_USE_SHELL` | Enable shell syntax for `local_command`; requires unsafe mode | `false` |
| `XIAOZHI_LOCAL_COMMAND_TIMEOUT_SEC` | Max seconds per local command | `5` |
| `XIAOZHI_LOCAL_COMMAND_CHECK_INTERVAL_SEC` | Minimum seconds between running-job `checkCommand` responses | `5` |
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
locally without a shell, and returns `stdout`, `stderr`, and `exit_code`. If the
command is still running after `XIAOZHI_LOCAL_COMMAND_TIMEOUT_SEC`, it continues
in the background and returns `status=running` with a `job_id`; use
`checkCommand` to read the latest output or final result, and `stopCommand` to
stop it.
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
