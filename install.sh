#!/bin/bash
set -e

echo "=== Antigravity Multi-License Orchestrator Installer ==="

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
SHARE_DIR="${HOME}/.local/share/agy-manager"
CONFIG_DIR="${HOME}/.config/agy-switcher"

mkdir -p "${BIN_DIR}" "${CONFIG_DIR}"

# 1. Sync source files to ~/.local/share/agy-manager if running from another location
if [ "${SRC_DIR}" != "${SHARE_DIR}" ]; then
    echo "Installing package to ${SHARE_DIR}..."
    mkdir -p "${SHARE_DIR}"
    rsync -av --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' "${SRC_DIR}/" "${SHARE_DIR}/"
fi

# 2. Install CLI wrappers
echo "Installing CLI commands to ${BIN_DIR}..."
cp "${SHARE_DIR}/bin/agy-switch" "${BIN_DIR}/agy-switch"
cp "${SHARE_DIR}/bin/agy-hub" "${BIN_DIR}/agy-hub"
chmod +x "${BIN_DIR}/agy-switch" "${BIN_DIR}/agy-hub"

# 3. Compile native macOS MenuBar app if swiftc is available
if command -v swiftc >/dev/null 2>&1 && [ "$(uname -s)" = "Darwin" ]; then
    echo "Compiling native macOS MenuBar app..."
    bash "${SHARE_DIR}/menubar/build.sh" || echo "Note: MenuBar compilation skipped (optional)."
fi

# 4. Install launchd agents on macOS
if [ "$(uname -s)" = "Darwin" ]; then
    LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
    mkdir -p "${LAUNCH_AGENTS_DIR}"

    for tmpl in "${SHARE_DIR}/launchd"/*.plist.template; do
        if [ -f "$tmpl" ]; then
            base="$(basename "$tmpl" .template)"
            target="${LAUNCH_AGENTS_DIR}/${base}"
            sed "s|{{HOME}}|${HOME}|g" "$tmpl" > "$target"
            echo "Installed ${target}"
            launchctl unload "$target" 2>/dev/null || true
            launchctl load -w "$target" 2>/dev/null || true
        fi
    done
fi

# 5. Import existing accounts if any
echo "Scanning for existing Antigravity accounts..."
PYTHONPATH="${SHARE_DIR}" python3 -c "from agy_manager.profiles import ProfileManager; pm = ProfileManager(); count = pm.import_existing_profiles(); print(f'Imported {count} active profile(s).')" || true

echo ""
echo "✓ Installation complete!"
echo "• Switch license: agy-switch"
echo "• Auto-select best Pro: agy-switch auto"
echo "• Web Dashboard: agy-hub (or visit http://127.0.0.1:3099)"
