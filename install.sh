#!/bin/bash
# Install system dependencies and Python virtual environment for whisplay-xiaozhi.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

echo "=== Installing system dependencies ==="
sudo apt-get update
sudo apt-get install -y \
    python3 python3-venv python3-pip \
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

# openwakeword is optional
pip install openwakeword 2>/dev/null || echo "openwakeword install failed (optional)"

echo "=== Downloading fonts and emojis ==="
cd "$SCRIPT_DIR/assets"
if command_exists wget; then
    if [ ! -f "NotoSansSC-Bold.ttf" ]; then
        wget -O NotoSansSC-Bold.ttf https://storage.whisplay.ai/whisplay-ai-chatbot/NotoSansSC-Bold.ttf
    else
        echo "NotoSansSC-Bold.ttf already exists, skip download."
    fi

    if [ ! -f "emoji_svg.zip" ]; then
        wget -O emoji_svg.zip https://storage.whisplay.ai/whisplay-ai-chatbot/emoji_svg.zip
    else
        echo "emoji_svg.zip already exists, skip download."
    fi
elif command_exists curl; then
    if [ ! -f "NotoSansSC-Bold.ttf" ]; then
        curl -fL -o NotoSansSC-Bold.ttf https://storage.whisplay.ai/whisplay-ai-chatbot/NotoSansSC-Bold.ttf
    else
        echo "NotoSansSC-Bold.ttf already exists, skip download."
    fi

    if [ ! -f "emoji_svg.zip" ]; then
        curl -fL -o emoji_svg.zip https://storage.whisplay.ai/whisplay-ai-chatbot/emoji_svg.zip
    else
        echo "emoji_svg.zip already exists, skip download."
    fi
else
    echo "Neither wget nor curl is installed."
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

echo "=== Done ==="
echo "Run: bash run.sh"
echo "On first boot, the LCD will show a verification code — enter it on xiaozhi.me to pair."
