#!/bin/bash
# Install whisplay-xiaozhi as a systemd service with auto-start on boot.

set -e

# Prevent running as root directly
TARGET_USER=$(whoami)
if [ "$TARGET_USER" == "root" ]; then
    echo "Error: Please run this script as your normal user (WITHOUT sudo)."
    echo "The script will ask for sudo permissions when needed."
    exit 1
fi

USER_HOME=$HOME
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="whisplay-xiaozhi"

echo "========================================"
echo " Whisplay XiaoZhi — Service Setup"
echo "========================================"
echo "User:       $TARGET_USER"
echo "Home:       $USER_HOME"
echo "Project:    $SCRIPT_DIR"
echo "========================================"

# Optional: disable graphical interface for headless setup
if [ "$(systemctl get-default)" == "graphical.target" ]; then
    echo "Graphical interface is currently enabled."
    read -p "Disable graphical interface for headless setup? (y/n) " disable_gui
    if [[ "$disable_gui" == "y" ]]; then
        sudo systemctl set-default multi-user.target
        echo "Graphical interface disabled."
    fi
fi

# Create the service file
echo "Creating systemd service file..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Whisplay XiaoZhi AI Voice Client
After=network-online.target sound.target
Wants=network-online.target sound.target

[Service]
Type=simple
User=$TARGET_USER
Group=audio
SupplementaryGroups=audio video gpio spi

WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/venv/bin/python3 $SCRIPT_DIR/main.py

Environment=PYTHONUNBUFFERED=1
Environment=HOME=$USER_HOME
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u $TARGET_USER)

PrivateDevices=no

StandardOutput=append:$SCRIPT_DIR/chatbot.log
StandardError=append:$SCRIPT_DIR/chatbot.log

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}.service
sudo systemctl restart ${SERVICE_NAME}.service

echo ""
echo "Done! Service installed and started."
echo ""
echo "Useful commands:"
echo "  sudo systemctl status $SERVICE_NAME    # Check status"
echo "  sudo systemctl restart $SERVICE_NAME   # Restart"
echo "  sudo systemctl stop $SERVICE_NAME      # Stop"
echo "  sudo journalctl -u $SERVICE_NAME -f    # Follow logs"
echo "  tail -f $SCRIPT_DIR/chatbot.log        # View log file"
echo ""

sleep 2
sudo systemctl status ${SERVICE_NAME} --no-pager
