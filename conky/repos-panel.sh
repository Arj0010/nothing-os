#!/usr/bin/env bash
#
# repos-panel.sh — emit the REPOS :// SYNC panel markup for conky.
#
# Cats the panel written by sync-repos.sh, prepending a warning if the sync
# itself has died. Staleness must be checked here, at render time: if the timer
# stops firing, nothing rewrites panel.txt and it would silently show old data.

PANEL="${XDG_CACHE_HOME:-$HOME/.cache}/repo-sync/panel.txt"
INTERVAL=7200                 # must match repo-sync.timer
STALE_AFTER=$((INTERVAL * 3))

if [[ ! -f "$PANEL" ]]; then
  printf '${color3}${font DejaVu Sans Mono:size=9}●${font}${color} ${color1}REPOS :// SYNC${color}\n'
  printf '${voffset 8}${color2}${hr 1}${color}\n'
  printf '${voffset 8}${color2}» waiting for first sync${color}\n'
  exit 0
fi

age=$(( $(date +%s) - $(stat -c %Y "$PANEL") ))

if (( age > STALE_AFTER )); then
  if (( age < 86400 )); then
    printf '${color3}⚠ STALE — last sync %sh ago${color}\n' "$(( age / 3600 ))"
  else
    printf '${color3}⚠ STALE — last sync %sd ago${color}\n' "$(( age / 86400 ))"
  fi
fi

cat "$PANEL"
