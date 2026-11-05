#!/usr/bin/env bash
# Binds Alt+Q (and Ctrl+Alt+O as a fallback) in GNOME to "look up the selected/copied text in Ocean".
#   ./install_shortcut_linux.sh           add or update the shortcuts
#   ./install_shortcut_linux.sh --remove  remove them
# Other desktops: bind any key to:  <this folder>/run.sh --lookup
set -euo pipefail
cd "$(dirname "$0")"

SCHEMA=org.gnome.settings-daemon.plugins.media-keys
KEY_BASE=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings
# "name=binding": the first is the main shortcut (OCEAN_SHORTCUT overrides it); the second is the older one.
SHORTCUTS=("ocean-lookup=${OCEAN_SHORTCUT:-<Alt>q}" "ocean-lookup-fallback=<Control><Alt>o")

if ! command -v gsettings >/dev/null || ! gsettings list-keys "$SCHEMA" >/dev/null 2>&1; then
    echo "GNOME settings not found. Bind a key to: $PWD/run.sh --lookup" >&2
    exit 1
fi

paths=()
for entry in "${SHORTCUTS[@]}"; do paths+=("$KEY_BASE/${entry%%=*}/"); done

current="$(gsettings get "$SCHEMA" custom-keybindings)"
# The list is a GVariant string array, e.g. @as [] or ['/path/a/', '/path/b/'].
updated="$(python3 - "$current" "${1:-}" "${paths[@]}" <<'EOF'
import ast, sys
current, mode, ours = sys.argv[1], sys.argv[2], sys.argv[3:]
items = [p for p in ast.literal_eval(current.replace("@as ", "", 1)) if p not in ours]
if mode != "--remove":
    items += ours
print("[" + ", ".join(repr(p) for p in items) + "]")
EOF
)"
gsettings set "$SCHEMA" custom-keybindings "$updated"

if [ "${1:-}" = "--remove" ]; then
    for path in "${paths[@]}"; do gsettings reset-recursively "$SCHEMA.custom-keybinding:$path"; done
    echo "Removed the Ocean lookup shortcuts."
    exit 0
fi

# gsettings parses values as GVariant text, so each string must be a quoted GVariant literal
# (a bare "'/path/run.sh' --lookup" fails with "expected end of input").
gvariant_str() { python3 -c 'import json, sys; print(json.dumps(sys.argv[1], ensure_ascii=False))' "$1"; }
command_line="$(python3 -c 'import shlex, sys; print(shlex.quote(sys.argv[1]) + " --lookup")' "$PWD/run.sh")"

for entry in "${SHORTCUTS[@]}"; do
    path="$KEY_BASE/${entry%%=*}/"
    binding="${entry#*=}"
    gsettings set "$SCHEMA.custom-keybinding:$path" name "$(gvariant_str "Ocean: look up selection")"
    gsettings set "$SCHEMA.custom-keybinding:$path" command "$(gvariant_str "$command_line")"
    gsettings set "$SCHEMA.custom-keybinding:$path" binding "$(gvariant_str "$binding")"
    echo "Shortcut $binding -> Ocean lookup installed."
done
echo "Select or copy text anywhere, then press it."
