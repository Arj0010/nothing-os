#!/bin/bash
# @vicinae.schemaVersion 1
# @vicinae.title Connect to Wi-Fi
# @vicinae.description Scan for networks and pick one to join
# @vicinae.mode silent
# @vicinae.icon 📡
# @vicinae.packageName Network

nmcli radio wifi on >/dev/null 2>&1
nmcli dev wifi rescan >/dev/null 2>&1
sleep 1

# One row per SSID, strongest signal first, drop blanks/dupes.
mapfile -t rows < <(nmcli -t -f SSID,SIGNAL,SECURITY dev wifi list 2>/dev/null \
  | awk -F: 'length($1)>0' | sort -t: -k2 -rn | awk -F: '!seen[$1]++')

[ ${#rows[@]} -eq 0 ] && { zenity --info --text="No Wi-Fi networks found." 2>/dev/null; exit 0; }

choice=$(printf '%s\n' "${rows[@]}" \
  | awk -F: '{print $1"\n"$2"%\n"($3==""?"open":$3)}' \
  | zenity --list --title="Wi-Fi Networks" --width=440 --height=440 \
      --column="Network" --column="Signal" --column="Security" 2>/dev/null)

[ -z "$choice" ] && exit 0

if nmcli -t -f NAME connection show 2>/dev/null | grep -qxF "$choice"; then
  nmcli connection up id "$choice" >/dev/null 2>&1
  notify-send "Wi-Fi" "Reconnected to $choice" 2>/dev/null
  exit 0
fi

sec=$(printf '%s\n' "${rows[@]}" | awk -F: -v s="$choice" '$1==s{print $3}')
if [ -n "$sec" ]; then
  pass=$(zenity --password --title="Password · $choice" 2>/dev/null)
  [ -z "$pass" ] && exit 0
  out=$(nmcli dev wifi connect "$choice" password "$pass" 2>&1)
else
  out=$(nmcli dev wifi connect "$choice" 2>&1)
fi

if echo "$out" | grep -qi "successfully"; then
  notify-send "Wi-Fi" "Connected to $choice" 2>/dev/null
else
  zenity --error --text="$out" 2>/dev/null
fi
