#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CRATE_DIR="$PROJECT_DIR/rust/watercolor_renderer"
case "$(uname -m)" in
    aarch64|arm64) ARCH_DIR="linux-aarch64" ;;
    armv7l|armv7) ARCH_DIR="linux-armv7l" ;;
    armv6l|armv6) ARCH_DIR="linux-armv6l" ;;
    *)
        echo "unsupported Raspberry Pi architecture: $(uname -m)" >&2
        exit 1
        ;;
esac
PREBUILT_DIR="$CRATE_DIR/prebuilt/$ARCH_DIR"
PREBUILT_OUTPUT="$PREBUILT_DIR/_watercolor_rust.so"
RUNTIME_OUTPUT="$PROJECT_DIR/display/_watercolor_rust.so"

if ! command -v cargo >/dev/null 2>&1; then
    echo "cargo is required to build the Rust watercolor renderer" >&2
    exit 1
fi

cargo build --manifest-path "$CRATE_DIR/Cargo.toml" --release
mkdir -p "$PREBUILT_DIR"
cp "$CRATE_DIR/target/release/lib_watercolor_rust.so" "$PREBUILT_OUTPUT"
cp "$PREBUILT_OUTPUT" "$RUNTIME_OUTPUT"
echo "Archived $PREBUILT_OUTPUT ($(uname -m))"
echo "Installed $RUNTIME_OUTPUT"
