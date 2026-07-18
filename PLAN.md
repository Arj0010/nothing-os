# Nothing OS · Cinnamon — Interactive Rebuild Plan

Goal: replace the passive Conky widgets with **interactive, floating, movable** Nothing-style
widgets (eww), refine the **theme colour + wallpaper**, make the **taskbar minimal**, and
finish **OpenWhispr**. Keep the Conky **animated breathing background** as the wallpaper layer.

---

## Phase 1 — eww (interactive widgets engine)
- **Install eww** (GTK widget system, X11).
  - Try prebuilt binary from `elkowar/eww` releases → drop in `~/.local/bin`.
  - If no binary: install Rust + GTK3 dev libs and `cargo build --release` (one bigger `sudo`).
- **Movable / floating:** run each widget as a borderless *normal* window with skip-taskbar/
  skip-pager hints and "always below". On Cinnamon you **move them with Alt + drag**, and they
  stay out of Alt-Tab. (True free-drag desktop widgets aren't native to Linux; this is the clean
  way to get floating + movable + interactive.)
- Config: `~/.config/eww/eww.yuck` (widgets) + `eww.scss` (Nothing styling, real Ndot/NType fonts).

## Phase 2 — the widgets (each floating, movable, clickable)
1. **Clock / date** — big Ndot, click → opens calendar.
2. **Quick Controls** — Wi-Fi · Bluetooth · Airplane · DND tiles. **Tap = toggle** (wired to the
   `nmcli`/`rfkill`/`bluetoothctl` scripts already built). Live state colour (red = on).
3. **System** — CPU / RAM / TMP / BAT live meters; click → opens system monitor.
4. **Weather** — wttr.in, click → refresh.
5. **Now Playing** — track + ⏮ ⏯ ⏭ buttons (playerctl).
6. **Launcher dock** — Nothing-style app tiles (Brave, Code, Terminal, Files, OpenWhispr…), click = launch.
7. **Agent · System://Status** — uptime, load, service dots (live).

## Phase 3 — theme colour + wallpaper (done "properly")
- **Accent:** keep Nothing red `#D71921` (default) — or switch to your pick (white-only / amber / cyan).
- **Wallpaper:** regenerate a cleaner dot-grid + subtle glyph, matched to the accent.
- **Icons:** switch to a cleaner minimal set — `Papirus-Dark` (crisp, consistent) or `ubuntu-mono-dark`
  (near-monochrome) for the Nothing feel.
- Keep GTK/WM theme dark; ensure accent is consistent across panel, widgets, wallpaper.

## Phase 4 — minimal taskbar (Cinnamon panel)
- Thin panel (~26–28px), dark, red accent.
- Trim applets to essentials (menu · window-list · systray · clock).
- Optional intelligent auto-hide for a cleaner desktop.

## Phase 5 — OpenWhispr finish
- App is open. Set **Whisper `base`** model + push-to-talk hotkey + AI formatting (API key).
- **ydotool typing fix** (enable `ydotoold` + `/dev/uinput` udev rule) so dictation types into apps.

## Phase 6 — wire-up & archive
- Autostart eww on login (replace the Conky-widgets autostart; keep Conky bg autostart).
- Copy all eww configs + wallpaper + theme notes into `~/nothing-os/`.

---

### Decisions needed before building
1. **Accent colour** — keep Nothing red, or change it?
2. **eww install** — OK to install a prebuilt binary, and fall back to a Rust build (bigger install) if needed?
3. **Icons** — Papirus-Dark (clean colour) vs ubuntu-mono-dark (monochrome)?

### What stays / goes
- KEEP: Conky animated breathing background · Vicinae Wi-Fi/BT commands · fonts · dark theme.
- REPLACE: passive Conky `widgets.conf` → interactive eww widgets.
