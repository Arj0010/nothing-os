#!/usr/bin/env bash
# One-time setup: earlyoom kills the biggest memory hog BEFORE the system freezes.
# Run: pkexec bash ~/guardian/earlyoom/setup.sh
set -euo pipefail
here="$(dirname "$(readlink -f "$0")")"

apt-get install -y earlyoom
install -m644 "$here/earlyoom.default" /etc/default/earlyoom
systemctl enable earlyoom
systemctl reset-failed earlyoom || true
systemctl restart earlyoom
systemctl --no-pager status earlyoom | head -12
