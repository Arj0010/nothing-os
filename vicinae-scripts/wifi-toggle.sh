#!/bin/bash
# @vicinae.schemaVersion 1
# @vicinae.title Toggle Wi-Fi
# @vicinae.description Turn the Wi-Fi radio on or off
# @vicinae.mode inline
# @vicinae.icon 📶
# @vicinae.packageName Network

state=$(nmcli -t -f WIFI radio 2>/dev/null)
if [ "$state" = "enabled" ]; then
  nmcli radio wifi off
  echo "Wi-Fi ● OFF"
else
  nmcli radio wifi on
  echo "Wi-Fi ● ON"
fi
