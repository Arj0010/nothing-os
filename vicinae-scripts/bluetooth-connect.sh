#!/bin/bash
# @vicinae.schemaVersion 1
# @vicinae.title Connect Bluetooth Device
# @vicinae.description Scan for devices and pick one to connect
# @vicinae.mode silent
# @vicinae.icon 🎧
# @vicinae.packageName Network

rfkill unblock bluetooth 2>/dev/null
bluetoothctl power on >/dev/null 2>&1
# discover nearby devices for a few seconds
timeout 6 bluetoothctl scan on >/dev/null 2>&1

mapfile -t devs < <(bluetoothctl devices 2>/dev/null | sed 's/^Device //')
[ ${#devs[@]} -eq 0 ] && { zenity --info --text="No Bluetooth devices found." 2>/dev/null; exit 0; }

choice=$(printf '%s\n' "${devs[@]}" \
  | awk '{mac=$1; $1=""; sub(/^ /,""); print mac"\n"$0}' \
  | zenity --list --title="Bluetooth Devices" --width=440 --height=440 \
      --column="MAC" --column="Name" --print-column=1 2>/dev/null)

[ -z "$choice" ] && exit 0

if bluetoothctl connect "$choice" | grep -qi "successful"; then
  notify-send "Bluetooth" "Connected $choice" 2>/dev/null
else
  notify-send "Bluetooth" "Could not connect $choice" 2>/dev/null
fi
