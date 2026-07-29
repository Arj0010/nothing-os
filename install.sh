#!/bin/bash
# Deploy this repo's config to the LIVE locations under ~/.config and ~/.local/bin.
# Use on a fresh machine (or to restore from backup), then log out/in or run start.sh.
set -e
R="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.config/nothing-widgets ~/.config/conky/nothing ~/.config/autostart ~/.local/bin ~/.config/vicinae

cp "$R/nothing-widgets/nothing-widgets.py" ~/.config/nothing-widgets/
cp "$R/nothing-widgets/cleaner_core.py" ~/.config/nothing-widgets/
cp "$R/conky/"* ~/.config/conky/nothing/ 2>/dev/null || true
cp "$R/nothing-conky.desktop" ~/.config/autostart/
install -m 755 "$R/bin/shot" ~/.local/bin/shot
[ -f "$R/config/vicinae-settings.json" ] && cp "$R/config/vicinae-settings.json" ~/.config/vicinae/settings.json || true

# Opt-in: let the CLEAN widget reclaim the apt cache and the journal without a
# password prompt (a focusless widget window cannot answer one). Validated with
# visudo before install — a malformed sudoers file locks you out of sudo entirely.
if [ "$1" = "--with-cleaner-sudo" ]; then
  echo "installing sudoers rule for the CLEAN widget (needs root)..."
  tmp="$(mktemp)"
  sed "s/^arjun /$USER /" "$R/config/nothing-cleaner.sudoers" > "$tmp"
  if sudo visudo -c -q -f "$tmp"; then
    sudo install -m 0440 -o root -g root "$tmp" /etc/sudoers.d/nothing-cleaner
    echo "  -> /etc/sudoers.d/nothing-cleaner"
  else
    echo "  !! sudoers file failed validation, NOT installed" >&2
  fi
  rm -f "$tmp"
fi

echo "installed repo -> live."
echo "launch now:  ~/.config/conky/nothing/start.sh"
echo "fonts: copy nothing-os/fonts/* into ~/.local/share/fonts and run: fc-cache -f"
echo "CLEAN widget root targets (apt/journal): ./install.sh --with-cleaner-sudo"
