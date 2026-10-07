#!/usr/bin/env bash
# Remove Guardian units, autostart entry, root helper and polkit rule. Keeps state for inspection.
set -euo pipefail
systemctl --user disable --now guardian.timer 2>/dev/null || true
rm -f ~/.config/systemd/user/guardian.service ~/.config/systemd/user/guardian.timer \
      ~/.config/autostart/guardian-env.desktop
systemctl --user daemon-reload
pkexec rm -f /usr/local/sbin/guardian-root-helper /etc/polkit-1/rules.d/50-guardian.rules
echo "Removed. State kept at ~/.local/state/guardian (delete it manually if you want)."
