#!/bin/bash
# Deploy this repo's config to the LIVE locations under ~/.config and ~/.local/bin.
# Use on a fresh machine (or to restore from backup), then log out/in or run start.sh.
set -e
R="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.config/nothing-widgets ~/.config/conky/nothing ~/.config/autostart ~/.local/bin ~/.config/vicinae

cp "$R/nothing-widgets/nothing-widgets.py" ~/.config/nothing-widgets/
cp "$R/conky/"* ~/.config/conky/nothing/ 2>/dev/null || true
cp "$R/nothing-conky.desktop" ~/.config/autostart/
install -m 755 "$R/bin/shot" ~/.local/bin/shot
[ -f "$R/config/vicinae-settings.json" ] && cp "$R/config/vicinae-settings.json" ~/.config/vicinae/settings.json || true

echo "installed repo -> live."
echo "launch now:  ~/.config/conky/nothing/start.sh"
echo "fonts: copy nothing-os/fonts/* into ~/.local/share/fonts and run: fc-cache -f"
