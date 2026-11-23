#!/bin/bash
# Rotate the Plus panel in a Raspberry Pi OS Wayland desktop session.
set -euo pipefail

case "${1:-}" in
    portrait) transform=normal ;;
    landscape) transform=270 ;;
    *)
        echo "Usage: bash tools/set-plus-orientation.sh portrait|landscape" >&2
        exit 2
        ;;
esac

cd "$(dirname "$0")/.."
if ! output="$(python3 -m hardware.whisplay_model)"; then
    echo "Whisplay Plus panel not detected." >&2
    exit 1
fi
wlr-randr --output "$output" --transform "$transform"
