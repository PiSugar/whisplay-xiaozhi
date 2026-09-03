#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CRATE_DIR="$PROJECT_DIR/rust/watercolor_renderer"
PREBUILT_DIR="$CRATE_DIR/prebuilt/linux-aarch64"
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
echo "Archived $PREBUILT_OUTPUT"
echo "Installed $RUNTIME_OUTPUT"
