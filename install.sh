#!/bin/bash
# Install system dependencies and Python virtual environment for whisplay-xiaozhi.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

register_daemon_app() {
    local socket_path="/tmp/whisplay-daemon.sock"
    local app_id="whisplay-xiaozhi"

    if [ ! -S "$socket_path" ]; then
        echo "whisplay-daemon socket not found, skip daemon app registration."
        return
    fi

    echo "=== Registering app to whisplay-daemon ==="
    python3 - <<EOF
import json
import socket

socket_path = "$socket_path"
payload = {
    "version": 1,
    "cmd": "app.register",
    "payload": {
        "app_id": "$app_id",
        "display_name": "xiaozhi",
        "icon": "AI",
        "launch_command": "bash $SCRIPT_DIR/run.sh",
        "cwd": "$SCRIPT_DIR",
        "persist": True,
    },
}

try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(socket_path)
        client.sendall((json.dumps(payload) + "\\n").encode("utf-8"))
        line = client.makefile("r").readline().strip()
    if not line:
        raise RuntimeError("empty response from whisplay-daemon")
    resp = json.loads(line)
    if resp.get("ok"):
        print("whisplay-daemon app.register success")
    else:
        print("whisplay-daemon app.register failed:", resp.get("error", "unknown"))
except Exception as e:
    print("whisplay-daemon app.register error:", e)
EOF
}

echo "=== Installing system dependencies ==="
sudo apt-get update
sudo apt-get install -y \
    python3 python3-venv python3-pip \
    python3-lgpio \
    sox libsox-fmt-all \
    libopus0 libopus-dev \
    libcairo2-dev libgirepository1.0-dev \
    unzip

echo "=== Creating Python virtual environment ==="
python3 -m venv venv
source venv/bin/activate

echo "=== Installing Python packages ==="
pip install --upgrade pip
pip install -r requirements.txt
pip install gpiod

echo "=== Ensuring gpiod runtime is available ==="
if ! python3 -c "import gpiod" >/dev/null 2>&1; then
    echo "System python missing gpiod, trying apt package..."
    sudo apt-get install -y python3-gpiod || true
fi

# openwakeword is optional
pip install openwakeword 2>/dev/null || echo "openwakeword install failed (optional)"

echo "=== Downloading fonts and emojis ==="
cd "$SCRIPT_DIR/assets"
if command_exists wget; then
    wget -O NotoSansSC-Bold.ttf https://storage.whisplay.ai/whisplay-ai-chatbot/NotoSansSC-Bold.ttf

    if [ ! -f "emoji_svg.zip" ]; then
        wget -O emoji_svg.zip https://storage.whisplay.ai/whisplay-ai-chatbot/emoji_svg.zip
    else
        echo "emoji_svg.zip already exists, skip download."
    fi
elif command_exists curl; then
    curl -fL -o NotoSansSC-Bold.ttf https://storage.whisplay.ai/whisplay-ai-chatbot/NotoSansSC-Bold.ttf

    if [ ! -f "emoji_svg.zip" ]; then
        curl -fL -o emoji_svg.zip https://storage.whisplay.ai/whisplay-ai-chatbot/emoji_svg.zip
    else
        echo "emoji_svg.zip already exists, skip download."
    fi
else
    echo "Neither wget nor curl is installed."
    exit 1
fi

if [ ! -s "NotoSansSC-Bold.ttf" ]; then
    echo "Error: required font NotoSansSC-Bold.ttf is missing or empty."
    exit 1
fi
# overwrite if exists
unzip -o emoji_svg.zip
cd "$SCRIPT_DIR"

echo "=== Creating .env from template ==="
if [ ! -f .env ]; then
    cp .env.template .env
    echo "Created .env from template"
else
    echo ".env already exists, skipping"
fi

register_daemon_app

echo "=== Done ==="
echo "Run: bash run.sh"
echo "On first boot, the LCD will show a verification code — enter it on xiaozhi.me to pair."
