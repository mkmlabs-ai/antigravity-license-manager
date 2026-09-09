#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_BIN="${HOME}/.local/bin/agy-menubar"

mkdir -p "${HOME}/.local/bin"
echo "Compiling native macOS MenuBar app (main.swift)..."
swiftc -O "${SCRIPT_DIR}/main.swift" -o "${OUTPUT_BIN}"
chmod +x "${OUTPUT_BIN}"
echo "Compiled successfully to ${OUTPUT_BIN}"
