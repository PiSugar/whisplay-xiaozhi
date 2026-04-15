# Whisplay XiaoZhi

[English](README.md)

<img width="200" alt="68747470733a2f2f646f63732e706973756761722e636f6d2f696d672f77686973706c61795f6c6f676f4034782d382e706e67" src="https://github.com/user-attachments/assets/b168a14c-71d4-473d-9fd9-196802bfc5e9" />

基于树莓派 + Whisplay HAT + PiSugar 电池的小智AI语音客户端。

通过 WebSocket 连接[小智AI平台](https://xiaozhi.me)，实现完整的语音交互流程：语音识别（ASR）、大模型对话（LLM）、语音合成（TTS），一个口袋大小的AI语音助手。

## 功能

- **WebSocket 语音对话** — 实现小智协议 v1，Opus 音频编解码
- **自动配对** — 设备在 LCD 上显示验证码，在 xiaozhi.me 输入即可绑定（无需手动填写 Token）
- **按键唤醒** — 按下按键唤醒设备并开始自动聆听（服务端 VAD 控制语音结束）
- **LCD 显示** — 240×280 ST7789V，显示状态、表情、滚动文字、电量
- **RGB LED** — 根据状态自动变色（空闲/聆听/思考/回答/错误）
- **电池监测** — PiSugar 电池电量实时显示
- **唤醒词** — 支持 openwakeword 免摆键唤醒
- **MCP 支持** — 服务端工具调用（JSON-RPC 2.0）

## 硬件需求

| 组件 | 说明 |
|------|------|
| 树莓派 | Zero 2W / Pi 4 / Pi 5 |
| Whisplay HAT | PiSugar Whisplay HAT（LCD + 麦克风 + 扬声器 + RGB LED + 按键） |
| PiSugar 电池 | 1200mAh / 5000mAh |
| WM8960 | 音频编解码器（HAT 自带） |

## 快速开始

### 1. 安装

```bash
git clone https://github.com/PiSugar/whisplay-xiaozhi.git
cd whisplay-xiaozhi
bash install.sh
```

### 2. 配置

复制配置模板并按需修改：

```bash
cp .env.template .env
```

大部分配置开箱即用。设备会自动检测 MAC 地址并与服务器配对。

### 3. 运行

```bash
bash run.sh
```

### 4. 首次配对

首次启动时，LCD 屏幕会显示一个**验证码**（如 `123456`）。

1. 访问 [xiaozhi.me](https://xiaozhi.me) 并登录
2. 添加新设备，输入 LCD 上显示的验证码
3. 绑定成功后，设备自动连接，即可使用

配对凭证会保存在本地，后续启动无需重新配对。

### 5. 使用

- **按下按钮** → 唤醒设备，开始自动聆听（服务端 VAD 自动检测语音结束）
- **回答过程中按下按钮** → 打断当前回答，开始新对话
- **唤醒词** → 效果同按下按钮（需在配置中启用）

## 项目结构

```
whisplay-xiaozhi/
├── main.py                 # 入口文件
├── config.py               # 配置管理（读取 .env）
├── application.py          # 主状态机
├── protocol/
│   ├── websocket_client.py # 小智 WebSocket 协议客户端
│   └── mcp_handler.py      # MCP 工具调用处理
├── audio/
│   ├── audio_codec.py      # Opus 编解码
│   ├── audio_recorder.py   # 麦克风录音（sox）
│   └── audio_player.py     # 扬声器播放（sox）
├── hardware/
│   ├── whisplay_board.py   # Whisplay HAT 硬件抽象
│   ├── battery.py          # PiSugar 电池监测
│   └── led_controller.py   # RGB LED 控制
├── display/
│   ├── ui_renderer.py      # LCD UI 渲染（30 FPS）
│   └── text_utils.py       # 文字/表情渲染工具
├── wakeword/
│   └── detector.py         # 唤醒词检测
├── iot/
│   ├── thing.py            # IoT 设备基类
│   └── thing_manager.py    # IoT 设备管理
├── assets/
│   ├── emoji_svg/          # Emoji SVG 图标
│   └── logo.png            # 启动 Logo
├── service/
│   └── whisplay-xiaozhi@.service  # systemd 服务
├── requirements.txt
├── install.sh
├── run.sh
├── .env.template
└── README.md
```

## 配置说明

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `XIAOZHI_OTA_URL` | OTA / 激活 API 地址 | `https://api.tenclass.net/xiaozhi/ota/` |
| `XIAOZHI_DEVICE_ID` | 设备ID（留空自动获取MAC） | — |
| `ALSA_INPUT_DEVICE` | ALSA 录音设备 | `default` |
| `ALSA_OUTPUT_DEVICE` | ALSA 播放设备 | `default` |
| `WAKE_WORD_ENABLED` | 启用唤醒词 | `false` |
| `WAKE_WORDS` | 唤醒词列表（逗号分隔） | `hey_jarvis` |
| `LCD_BRIGHTNESS` | LCD 亮度 (0-100) | `100` |
| `PISUGAR_ENABLED` | 启用电池监测 | `true` |

## 开机自启

```bash
# 安装 systemd 服务（替换 pi 为你的用户名）
sudo cp service/whisplay-xiaozhi@.service /etc/systemd/system/
sudo systemctl enable whisplay-xiaozhi@pi
sudo systemctl start whisplay-xiaozhi@pi

# 查看日志
sudo journalctl -u whisplay-xiaozhi@pi -f
```

## 协议参考

本项目实现了小智 ESP32 WebSocket 协议 v1：
- [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)
- [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi)
- [OTA 激活](https://my.feishu.cn/wiki/FjW6wZmisimNBBkov6OcmfvknVd) 设备通过 HTTP 注册，用户输入验证码绑定
- [协议文档](https://my.feishu.cn/wiki/M0XiwldO9iJwHikpXD5cEx71nKh) WebSocket 连接 + Hello 握手 + Opus 音频流 + JSON 控制消息

## License

GPL-3.0
