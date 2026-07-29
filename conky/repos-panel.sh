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

RULE="\${color2}$(printf '·%.0s' {1..52})\${color}"

if [[ ! -f "$PANEL" ]]; then
  printf '${color3}${font DejaVu Sans Mono:size=9}●${font}${color} ${color1}REPOS :// SYNC${color}\n'
  printf '${voffset 6}%s\n' "$RULE"
  printf '${voffset 6}${color2}» waiting for first sync${color}\n'
  exit 0
fi

# The sync preserves the last good panel during an outage rather than blanking
# it, and drops this marker instead. Surface that as a banner so the numbers
# below are clearly understood as last-known-good, not current.
OFFLINE_MARK="${XDG_CACHE_HOME:-$HOME/.cache}/repo-sync/offline"
if [[ -f "$OFFLINE_MARK" ]]; then
  since=$(cut -f1 "$OFFLINE_MARK")
  printf '${color3}${font DejaVu Sans Mono:size=9}⚠${font} OFFLINE since %s${color}\n' \
    "$(date -d "@$since" +%H:%M 2>/dev/null || echo '?')"
fi

age=$(( $(date +%s) - $(stat -c %Y "$PANEL") ))

if (( age > STALE_AFTER )); then
  if (( age < 86400 )); then
    printf '${color3}${font DejaVu Sans Mono:size=9}⚠${font} STALE — last sync %sh ago${color}\n' "$(( age / 3600 ))"
  else
    printf '${color3}${font DejaVu Sans Mono:size=9}⚠${font} STALE — last sync %sd ago${color}\n' "$(( age / 86400 ))"
  fi
fi

cat "$PANEL"
