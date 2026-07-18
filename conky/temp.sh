#!/bin/bash
# hottest thermal zone, in whole °C
t=$(for z in /sys/class/thermal/thermal_zone*/temp; do cat "$z" 2>/dev/null; done | sort -rn | head -1)
echo $(( ${t:-0} / 1000 ))
