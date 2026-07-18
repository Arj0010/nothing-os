#!/bin/bash
# Cached wttr.in lookup.  Usage: weather.sh temp|cond|hum
CACHE="$HOME/.cache/nothing-weather"
FIELD="$1"
mkdir -p "$HOME/.cache"

stale=1
if [ -f "$CACHE" ]; then
  age=$(( $(date +%s) - $(stat -c %Y "$CACHE" 2>/dev/null || echo 0) ))
  [ "$age" -lt 900 ] && stale=0
fi
if [ "$stale" -eq 1 ]; then
  d=$(curl -fsS --max-time 8 "wttr.in/?format=%t|%C|%h" 2>/dev/null)
  [ -n "$d" ] && printf '%s' "$d" > "$CACHE"
fi

line=$(cat "$CACHE" 2>/dev/null)
IFS='|' read -r t c h <<< "$line"
case "$FIELD" in
  temp) echo "${t:-–}" ;;
  cond) echo "${c:-NO SIGNAL}" | tr '[:lower:]' '[:upper:]' ;;
  hum)  echo "${h:-–}" ;;
  *)    echo "${line:-–}" ;;
esac
