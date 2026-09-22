# Whisplay XiaoZhi

[中文版](README_CN.md)

<img width="200" alt="68747470733a2f2f646f63732e706973756761722e636f6d2f696d672f77686973706c61795f6c6f676f4034782d382e706e67" src="https://github.com/user-attachments/assets/b168a14c-71d4-473d-9fd9-196802bfc5e9" />

XiaoZhi AI voice client for Raspberry Pi + Whisplay HAT + PiSugar battery.

Connects to the [XiaoZhi AI platform](https://xiaozhi.me) using OTA-provided WebSocket or MQTT credentials, providing a complete voice interaction pipeline: ASR (speech recognition), LLM (language model), and TTS (text-to-speech) — all in a pocket-sized device. A direct WebSocket endpoint can also be configured for a self-hosted server.

## Features

- **WebSocket or MQTT Voice Conversation** — XiaoZhi protocol v1 with Opus audio codec
- **Auto Pairing** — Device shows a verification code on LCD; enter it on xiaozhi.me to bind (no token needed)
- **Push-to-Wake** — Button press wakes the device and starts auto-listening (server-side VAD controls when speech ends)
- **Optional Voice Barge-In** — sustained speech can interrupt TTS and immediately start a new turn
- **Configurable LCD UI** — classic status/emoji view or the audio-reactive watercolor orb ported from `whisplay-chatgpt`
- **RGB LED** — Automatic color changes based on state (idle / listening / thinking / speaking / error)
- **Battery Monitor** — Real-time PiSugar battery level display
- **whisplay-daemon Ready** — Auto-adapts to daemon framebuffer / button / LED mode when available
- **Wake Word** — Hands-free activation via openwakeword
- **MCP Support** — Server-side tool invocation (JSON-RPC 2.0)
- **Raspberry Pi Camera** — MCP photo capture uploaded to the server-provided vision service, with its analysis returned to the model

## Hardware Requirements

| Component | Description |
|-----------|-------------|
| Raspberry Pi | Zero 2W / Pi 4 / Pi 5 |
| Whisplay HAT | PiSugar Whisplay HAT (LCD + mic + speaker + RGB LED + button) |
| PiSugar Battery | 1200mAh / 5000mAh |
| WM8960 | Audio codec (built into HAT) |
| Raspberry Pi Camera | Camera supported by `rpicam-still`/`rpicam-vid` or their `libcamera-*` equivalents (optional) |

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
│   ├── mqtt_client.py      # XiaoZhi MQTT + UDP protocol client
│   ├── camera_tool.py      # Camera capture, viewfinder, and vision upload
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
│   ├── ui_renderer.py      # Classic and configurable-FPS watercolor LCD UI
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

### Enable the watercolor orb

<p align="center">
  <img src="assets/watercolor-orb.gif" alt="Audio-reactive watercolor orb rendered on Raspberry Pi Zero 2 W" width="176">
</p>

Copy the configuration template if `.env` does not exist, then enable the
watercolor UI. The following settings are the tested high-quality profile for
Zero 2 W and newer Raspberry Pi models:

```bash
cp -n .env.template .env
```

```dotenv
DISPLAY_UI_STYLE=watercolor
WATERCOLOR_FPS=8
WATERCOLOR_RENDER_SCALE=0.60
WATERCOLOR_SMOOTH_FBM=true
WATERCOLOR_TEMPORAL_3D=true
WATERCOLOR_THREADS=2
```

Restart `whisplay-xiaozhi@pi` when using the standalone systemd service, or
exit and relaunch XiaoZhi from the Whisplay app menu when using
`whisplay-daemon`. Listening expands the orb with microphone energy; assistant
speech drives its internal pigment flow.

Set `BARGE_IN_ENABLED=true` to allow sustained speech to interrupt assistant
playback. If speaker echo causes false triggers, raise `BARGE_IN_MIN_RMS`.

### 3D robot workstation

![Robot workstation animation preview](assets/robot-workstation.gif)

Set these values in `.env` and restart the app:

```dotenv
DISPLAY_UI_STYLE=robot
ROBOT_FPS=30
ROBOT_SLEEP_AFTER=45
```

A small isometric voxel workstation sits in the middle of the screen, viewed over
the robot's right shoulder. The laptop faces the robot with the keyboard within
reach of its fixed-length articulated arms. The robot
breathes and occasionally turns toward the viewer, holding eye contact for
2.5–4.5 seconds with a blink and subtle head movement before turning back.
Turn timing, speed, and normal blinks vary randomly, with occasional alternating
foot swings or a 4.3-second stretch while sitting idle: arms rise, the torso leans
back gently with narrowed eyes, then relaxes. Stretching waits for glances and
foot swings to finish and yields smoothly to activity. After 45 idle seconds it
randomly chooses a nap or a pixel arcade game on its laptop. Games last 18–28
seconds, followed by 35–60 seconds of rest; prolonged idle alternates the two.
It naps with its left hand on the tabletop and its right arm hanging down.
Two other random idle gestures turn toward the viewer and wave, or lift the left
wrist and glance down at its illuminated display. Each lasts about 3.8 seconds,
does not overlap other idle gestures, and yields smoothly to real activity.
Connecting, activation, speech, and tool status trigger typing;
the thinking state raises its right hand to scratch its head while waiting for a
reply. Listening and photography wake it up. The laptop shows animated decorative terminal
text. Captured photos appear as edge-to-edge, center-cropped cards above the robot for
`CAMERA_PREVIEW_SECONDS`. Wi-Fi, battery, captions and caption paging reuse the
watercolor layout and `WATERCOLOR_CAPTION_*` settings.
Robot and watercolor modes extract inline `%tool.name...` markers into a separate
blue tag above captions, including repeat counts. Tool progress does not replace
captions or reset their paging timer.

A bright, screen-facing sleep “Z” floats well above the head. Hold the button for
0.65 seconds to orbit the camera 90° around the ground normal with a 0.9-second
eased transition. Each hold rotates once; four holds return to the original view.
Short-press wake and double-click photography remain available.
While typing, the head gently scans the screen. Random short thinking and drinking
breaks (reach, lift, sip, replace) interrupt typing without changing voice state;
actual listening, thinking and emergency events take priority.

Occasional cosmetic events are enabled by default: a smoking laptop catches fire,
then the startled robot turns and bends to retrieve a red extinguisher from under
its chair, aims a visible foam jet at the fire and puts the cylinder back.
In rain it looks up, flinches and shields its head before fetching an umbrella;
the canopy stays centered above its head while the other hand resumes typing.
The scenes last about 13 and 16 seconds, never overlap, and restore the normal pose afterward.
The first event starts after 18–35 eligible awake seconds, followed by 40–90 second
gaps. Sleeping pauses scheduling; listening, thinking, tool status and photo previews
fade out an active event within about 0.45 seconds. Set `ROBOT_EVENTS_ENABLED=false`
to disable them. They do not change conversation or audio state.

![Random workstation events](assets/robot-events.gif)

Preview both complete events with
`python tools/preview_robot.py --events --output /tmp/robot-events.gif`.

Rust handles geometry, animation, depth buffering, 2x supersampling, and RGB565
output with the Python GIL released. Static scene pixels and depth are cached.
`ROBOT_FPS` is a target; actual frame rate depends on Pi load and SPI bandwidth.
The Linux AArch64 prebuilt includes both watercolor and robot renderers; compatible
systems need no on-device build. When upgrading an older deployed extension,
copy `rust/watercolor_renderer/prebuilt/linux-aarch64/_watercolor_rust.so` over
`display/_watercolor_rust.so` and restart. See `BUILD.md` alongside the prebuilt
for build provenance and compatibility. On other platforms, first run
`bash tools/build_watercolor_rust.sh`. The script also supports macOS previews:

```bash
python tools/preview_robot.py --output /tmp/robot-workstation.gif
```

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
| `XIAOZHI_CLIENT_ID` | Client UUID (auto-generated if empty) | — |
| `XIAOZHI_WS_URL` | Direct WebSocket URL; bypasses OTA when set | — |
| `XIAOZHI_WS_TOKEN` | Token for direct WebSocket mode; may be empty if the server does not require authentication | — |
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
| `DISPLAY_UI_STYLE` | LCD UI: `classic`, `watercolor`, or `robot` | `classic` |
| `ROBOT_FPS` | Robot target frame rate, 1–60 | `30` |
| `ROBOT_SLEEP_AFTER` | Idle seconds before the robot sleeps, minimum 5 | `45` |
| `ROBOT_EVENTS_ENABLED` | Random fire/extinguisher and rain/umbrella scenes | `true` |
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
| `XIAOZHI_CAMERA_TOOL_ENABLED` | Expose the official `self.camera.take_photo` MCP tool | `false` |
| `XIAOZHI_CAMERA_COMMAND` | Still-camera command override; empty auto-detects `rpicam-still`/`libcamera-still` | — |
| `XIAOZHI_CAMERA_VIEWFINDER_COMMAND` | Live-view command override; empty auto-detects `rpicam-vid`/`libcamera-vid` | — |
| `XIAOZHI_CAMERA_INDEX` | Camera index passed to `rpicam-still` | `0` |
| `XIAOZHI_CAMERA_WIDTH` | Default capture width (160-1280) | `1280` |
| `XIAOZHI_CAMERA_HEIGHT` | Default capture height (120-960) | `960` |
| `XIAOZHI_CAMERA_QUALITY` | JPEG quality (30-95) | `90` |
| `XIAOZHI_CAMERA_WARMUP_MS` | Camera warm-up time before a still capture | `2000` |
| `XIAOZHI_CAMERA_TIMEOUT_SEC` | Still-capture process timeout | `15` |
| `XIAOZHI_CAMERA_VISION_TIMEOUT_SEC` | Vision service upload/analysis timeout | `60` |
| `XIAOZHI_CAMERA_VIEWFINDER_WIDTH` | Manual live-view width (320-1280) | `640` |
| `XIAOZHI_CAMERA_VIEWFINDER_HEIGHT` | Manual live-view height (240-960) | `480` |
| `XIAOZHI_CAMERA_VIEWFINDER_FPS` | Manual live-view frame rate | `5` |
| `XIAOZHI_CAMERA_VIEWFINDER_QUALITY` | Manual live-view JPEG quality (40-90) | `75` |
| `XIAOZHI_CAMERA_VIEWFINDER_READY_TIMEOUT_SEC` | Timeout waiting for the first live-view frame | `10` |
| `XIAOZHI_CAMERA_MAX_FRAME_BYTES` | Maximum accepted live-view JPEG frame size | `2097152` |
| `XIAOZHI_CAMERA_DOUBLE_CLICK_SECONDS` | Maximum interval for entering the viewfinder by double-click | `0.38` |
| `XIAOZHI_CAMERA_OUTPUT_DIR` | Local capture directory | `data/camera` |
| `XIAOZHI_CAMERA_KEEP_CAPTURES` | Number of recent automatic `capture-*` photos to keep | `10` |
| `XIAOZHI_CAMERA_AUTOFOCUS` | Trigger autofocus before capture (Camera Module 3/IMX708) | `true` |
| `XIAOZHI_CAMERA_AUTOFOCUS_RANGE` | Autofocus range: `normal`, `macro`, or `full` | `full` |
| `XIAOZHI_CAMERA_AUTOFOCUS_SPEED` | Autofocus speed: `normal` or `fast` | `fast` |
| `XIAOZHI_CAMERA_PREVIEW_SECONDS` | LCD preview duration for photos requested by XiaoZhi | `2` |
| `XIAOZHI_CAMERA_USER_PHOTO_PREVIEW_SECONDS` | LCD preview duration for button-captured photos | `2` |

This table covers the main settings. See [`.env.template`](.env.template) for additional deployable settings and inline guidance.

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

When `XIAOZHI_CAMERA_TOOL_ENABLED=true`, the device independently advertises
`self.camera.take_photo`. The tool captures a JPEG, uploads it to the authenticated
vision endpoint supplied during MCP initialization, and returns the vision service's
JSON or text analysis to the model as MCP text content; the JPEG itself is not returned
as an MCP image content block. Its required `question` argument tells the vision service
what to inspect. Passing `use_selected_photo=true` analyzes the latest button-captured
photo instead of taking a new one.

For manual capture, double-click the button to enter the live viewfinder and single-click
to save the current frame. The device then reconnects, starts listening, and temporarily
changes the camera tool description to encourage the hosted model to use the saved photo
for the next request. This is a tool-selection hint, not a protocol-level multimodal
attachment: if the hosted model does not call `self.camera.take_photo`, the photo is not
automatically included in that conversation turn. When the tool is called, the pending
photo is used before a new capture. Manual `user-photo-*` files are not removed by the
automatic `capture-*` rotation. The classic UI shows previews full-screen; watercolor
mode shows them inside the orb circle.

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
