#!/bin/bash
# Pull the LIVE desktop config from ~/.config into this repo (the backup source of truth).
# Run this after editing the live files, then git commit + push.
set -e
R="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$R/nothing-widgets" "$R/conky" "$R/bin" "$R/config"

cp ~/.config/nothing-widgets/nothing-widgets.py "$R/nothing-widgets/"
cp -r ~/.config/conky/nothing/. "$R/conky/"
cp ~/.config/autostart/nothing-conky.desktop "$R/nothing-conky.desktop"
cp ~/.local/bin/shot "$R/bin/shot"
cp ~/.config/vicinae/settings.json "$R/config/vicinae-settings.json" 2>/dev/null || true

# runtime/personal state stays out of the repo
rm -f "$R/nothing-widgets/positions.json" "$R/nothing-widgets/notes.txt" 2>/dev/null || true
echo "synced live -> repo. Now: git add -A && git commit && git push"
