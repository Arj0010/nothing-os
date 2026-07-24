#!/bin/bash
# theme-apply.sh "#RRGGBB"
# Retint the whole background to a palette accent: regenerate the wallpaper,
# point Cinnamon at it (cache-busted), rewrite the conky accent and reload it.
# Called by the PALETTE widget; safe to run standalone.
set -u
HEX="${1:?usage: theme-apply.sh '#RRGGBB'}"
CDIR="$HOME/.config/conky/nothing"

# --- 0..1 float components for the conky lua accent ---
h="${HEX#\#}"
r=$(printf '%.3f' "$(echo "scale=4; $((16#${h:0:2}))/255" | bc)")
g=$(printf '%.3f' "$(echo "scale=4; $((16#${h:2:2}))/255" | bc)")
b=$(printf '%.3f' "$(echo "scale=4; $((16#${h:4:2}))/255" | bc)")

# --- wallpaper: timestamped filename so gsettings actually notices the change ---
OUT="$CDIR/wallpaper-$(date +%s).png"
python3 "$CDIR/genwall.py" "$HEX" "$OUT" >/dev/null 2>&1 || exit 1
cp -f "$OUT" "$CDIR/wallpaper.png"                       # keep the stable name in sync
gsettings set org.cinnamon.desktop.background picture-uri "file://$OUT"
gsettings set org.cinnamon.desktop.background picture-options 'stretched' 2>/dev/null || true
# prune older generated wallpapers, keep the two newest
ls -1t "$CDIR"/wallpaper-*.png 2>/dev/null | tail -n +3 | xargs -r rm -f

# --- conky animation accent: rewrite the single AR,AG,AB line, then reload ---
sed -i -E "s/^local AR, AG, AB = .*/local AR, AG, AB = $r, $g, $b/" "$CDIR/bg.lua"
if pgrep -x conky >/dev/null 2>&1; then
  pkill -x conky >/dev/null 2>&1; sleep 0.3
fi
setsid conky -c "$CDIR/bg.conf" >/dev/null 2>&1 < /dev/null &
echo "theme-apply: $HEX -> ($r, $g, $b)"
