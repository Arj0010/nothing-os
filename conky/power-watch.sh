#!/bin/bash
# Runs the animated background only on AC power; static wallpaper shows on battery.
BGCONF="$HOME/.config/conky/nothing/bg.conf"

is_ac(){ [ "$(cat /sys/class/power_supply/ADP0/online 2>/dev/null)" = "1" ]; }
bg_running(){ pgrep -f "conky.*nothing/bg.conf" >/dev/null 2>&1; }

sync_bg(){
  if is_ac; then
    bg_running || conky -c "$BGCONF" >/dev/null 2>&1 &
  else
    pkill -f "conky.*nothing/bg.conf" >/dev/null 2>&1
  fi
}

sync_bg
while true; do
  sleep 20
  sync_bg
done
