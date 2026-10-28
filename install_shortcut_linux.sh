#!/usr/bin/env bash
# Binds Ctrl+Alt+O (GNOME) to "look up the selected/copied text in Ocean".
#   ./install_shortcut_linux.sh           add or update the shortcut
#   ./install_shortcut_linux.sh --remove  remove it
# Other desktops: bind any key to:  <this folder>/run.sh --lookup
set -euo pipefail
cd "$(dirname "$0")"

SCHEMA=org.gnome.settings-daemon.plugins.media-keys
KEY_PATH=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ocean-lookup/
BINDING="${OCEAN_SHORTCUT:-<Control><Alt>o}"

if ! command -v gsettings >/dev/null || ! gsettings list-keys "$SCHEMA" >/dev/null 2>&1; then
    echo "GNOME settings not found. Bind a key to: $PWD/run.sh --lookup" >&2
    exit 1
fi

current="$(gsettings get "$SCHEMA" custom-keybindings)"
# The list is a GVariant string array, e.g. @as [] or ['/path/a/', '/path/b/'].
updated="$(python3 - "$current" "$KEY_PATH" "${1:-}" <<'EOF'
import ast, sys
current, path, mode = sys.argv[1], sys.argv[2], sys.argv[3]
items = ast.literal_eval(current.replace("@as ", "", 1))
items = [p for p in items if p != path]
if mode != "--remove":
    items.append(path)
print("[" + ", ".join(repr(p) for p in items) + "]")
EOF
)"
gsettings set "$SCHEMA" custom-keybindings "$updated"

if [ "${1:-}" = "--remove" ]; then
    gsettings reset-recursively "$SCHEMA.custom-keybinding:$KEY_PATH"
    echo "Removed the Ocean lookup shortcut."
    exit 0
fi

gsettings set "$SCHEMA.custom-keybinding:$KEY_PATH" name "Ocean: look up selection"
gsettings set "$SCHEMA.custom-keybinding:$KEY_PATH" command "'$PWD/run.sh' --lookup"
gsettings set "$SCHEMA.custom-keybinding:$KEY_PATH" binding "$BINDING"
echo "Shortcut $BINDING -> Ocean lookup installed. Select or copy text anywhere, then press it."
