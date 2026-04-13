#!/bin/bash
# Run whisplay-xiaozhi.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ -d "venv" ]; then
    source venv/bin/activate
fi

exec python3 main.py "$@"
