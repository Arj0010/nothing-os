#!/usr/bin/env bash
# Guardian 10-day beta: save the daily report, then open a Claude session that fixes what Guardian got wrong.
# Edits are pre-approved; beta/verify.sh reverts the session if tests fail or safety files change.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
git checkout -q beta
day="$(date +%F)"
.venv/bin/guardian report --hours 24 > "beta/reports/$day.md"
n="$(find beta/reports -name '*.md' | wc -l)"
start="$(git rev-parse HEAD)"
claude="$(command -v claude || echo "$HOME/.local/bin/claude")"


notify-send -a Guardian -u critical "Guardian beta day $n/10" "Report saved. Claude is reviewing and fixing in a terminal."
# Pre-approved permissions live in beta/settings.json (acceptEdits + allow/deny lists).
x-terminal-emulator -e bash -c "cd '$PWD' && '$claude' \"\$(cat beta/PROMPT.md)\" --settings beta/settings.json; \
  beta/verify.sh '$start'; exec bash"
if [ "$n" -ge 10 ]; then
  systemctl --user disable guardian-beta.timer
fi
