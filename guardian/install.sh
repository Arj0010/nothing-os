#!/usr/bin/env bash
# Install Guardian: venv, systemd user timer, root helper + polkit rule (one password prompt).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -e .

mkdir -p ~/.config/systemd/user ~/.config/autostart
install -m644 systemd/guardian.service systemd/guardian.timer ~/.config/systemd/user/
install -m644 systemd/guardian-env.desktop ~/.config/autostart/
systemctl --user import-environment DISPLAY XAUTHORITY 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now guardian.timer

rules="$(mktemp)"
trap 'rm -f "$rules"' EXIT
sed "s/__USER__/$USER/" root-helper/50-guardian.rules > "$rules"
chmod 644 "$rules"
echo "Installing root helper + polkit rule (password prompt)..."
pkexec bash -c "install -m755 -o root -g root '$PWD/root-helper/guardian-root-helper' /usr/local/sbin/guardian-root-helper \
  && install -m644 -o root -g root '$rules' /etc/polkit-1/rules.d/50-guardian.rules"
echo "Guardian installed. Check: systemctl --user list-timers guardian.timer"
