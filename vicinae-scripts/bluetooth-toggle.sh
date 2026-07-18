#!/bin/bash
# @vicinae.schemaVersion 1
# @vicinae.title Toggle Bluetooth
# @vicinae.description Turn Bluetooth on or off
# @vicinae.mode inline
# @vicinae.icon 🔵
# @vicinae.packageName Network

powered=$(bluetoothctl show 2>/dev/null | awk -F': ' '/Powered:/{print $2; exit}')
if [ "$powered" = "yes" ]; then
  bluetoothctl power off >/dev/null 2>&1
  echo "Bluetooth ● OFF"
else
  rfkill unblock bluetooth 2>/dev/null
  bluetoothctl power on >/dev/null 2>&1
  echo "Bluetooth ● ON"
fi
