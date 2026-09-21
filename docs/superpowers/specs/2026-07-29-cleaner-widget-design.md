# CLEAN :// — cache / memory optimiser widget

Date: 2026-07-29
Status: approved, ready to implement

## Goal

A small, minimal Nothing-OS widget that reclaims disk space, surfaces memory hogs,
and cleans with one touch — without ever breaking a running app or a dev toolchain.

## Baseline (measured on this machine, 2026-07-29)

| Target | Size | Note |
|---|---|---|
| `/var/cache/apt` | 1.1 G | needs root |
| `~/.cache/puppeteer` | 643 M | held — deleting forces a ~600 MB re-download |
| systemd journal | 500 M | needs root |
| `~/.cache/uv` | 174 M | held — slows rebuilds |
| `~/.cache/BraveSoftware` | 128 M | safe |
| `~/.cache/vicinae` | 105 M | safe, but owned by a running service |
| `~/.cache/mesa_shader_cache` | 9 M | safe |
| Trash + thumbnails | ~2 M | safe |

RAM: 11 Gi total, 3.9 Gi used, 7.6 Gi available. The machine is not memory-starved,
so "memory boost" is framed as visibility + targeted kills, not cache dropping.

## Decisions

1. **CLEAN NOW runs the safe set only.** Held targets (puppeteer, uv) are scanned
   and displayed but never auto-cleaned — each has its own `[×]`.
2. **Root via a narrow sudoers rule**, opt-in at install time, pinned to exactly two
   commands. No password prompt into a focusless widget.
3. **No `drop_caches`.** On Linux it discards useful page cache and makes the machine
   slower right after. The widget reports top consumers and offers `SIGTERM` instead.
4. **History is sampled in-widget**, every 30 s, to a rolling 7-day JSONL. No extra
   systemd unit to install and keep in sync.
5. **Rail pop-out**, not a pane card — keeps the vertical pane uncluttered.

## Architecture

Two files, one clean boundary:

- **`nothing-widgets/cleaner_core.py`** — pure logic, zero GTK. Targets table,
  scanning, the path guard, deletion, `/proc` sampling, history store, aggregation,
  process revival. Importable and testable without a display.
- **`nothing-widgets/nothing-widgets.py`** — `Cleaner(Widget)` renders and wires
  buttons only. Existing helpers (`dots`, `spark`, `human`, `rule`, `L`, `vbox`) are
  reused so the card needs no new visual vocabulary.

This boundary exists because `nothing-widgets.py` is already ~2400 lines, and because
the risky part of this feature is file deletion — that belongs somewhere it can be
tested directly.

### Public surface of `cleaner_core`

| Function | Contract |
|---|---|
| `scan() -> list[Target]` | Sizes every target. Never raises; a failed target reports `err`. |
| `clean(keys) -> list[Result]` | Deletes contents of the named targets. Each target independent. |
| `sample() -> list[Proc]` | Top-N processes by RSS, read from `/proc`. |
| `History.append(sample)` / `.series()` | Rolling 7-day store, per-app peak + sparkline buckets. |
| `snapshot_watched()` / `revive(snapshot)` | Detect and relaunch processes killed by a clean. |
| `kill(pid)` | `SIGTERM` only, refuses own PID. |

## Layout (rail pop-out, 460 px)

```
CLEAN ://                            2.4G ●
─────────────────────────────────────────
        R E C L A I M A B L E
              2.4G
        ┌─────────────────┐
        │    CLEAN NOW    │
        └─────────────────┘
─────────────────────────────────────────
APT       1.1G  ●●●●●●●●●●●●
JOURNAL   500M  ●●●●●●
BRAVE     128M  ●●
VICINAE   105M  ●
MESA       9M   ·
TRASH      2M   ·
─────────────────────────────────────────
HELD · not auto-cleaned
PUPPETEER 643M                       [×]
UV        174M                       [×]
─────────────────────────────────────────
MEMORY · 7d                    peak
ollama    ▁▁▂▇▇▃▁▁▂▇▇▁        3.2G  [KILL]
brave     ▃▄▅▅▆▇▆▅▄▄▃▄        2.1G  [KILL]
code      ▂▂▃▃▄▄▄▃▃▄▄▃        1.4G  [KILL]
```

Rail icon `◌`, tag `CLEAN`. The header subtitle carries the live reclaimable total,
so the rail answers "is there anything to clean?" without opening the card.

## Targets

| Key | Path / command | Root | Auto | Owning service |
|---|---|---|---|---|
| `apt` | `apt-get clean` | yes | yes | — |
| `journal` | `journalctl --vacuum-size=100M` | yes | yes | — |
| `brave` | `~/.cache/BraveSoftware` | no | yes | — |
| `vicinae` | `~/.cache/vicinae` | no | yes | `vicinae.service` (user) |
| `mesa` | `~/.cache/mesa_shader_cache` | no | yes | — |
| `pip` | `~/.cache/pip` | no | yes | — |
| `mintinstall` | `~/.cache/mintinstall` | no | yes | — |
| `flatpak` | `~/.cache/flatpak` | no | yes | — |
| `thumbnails` | `~/.cache/thumbnails` | no | yes | — |
| `trash` | `~/.local/share/Trash/{files,info}` | no | yes | — |
| `puppeteer` | `~/.cache/puppeteer` | no | **held** | — |
| `uv` | `~/.cache/uv` | no | **held** | — |

## Safety rails

- **Path guard.** Every deletion path is `realpath`'d and asserted to sit under
  `~/.cache` or `~/.local/share/Trash`. Anything else raises before a single `unlink`.
  Symlinks pointing outside the allowed roots are rejected, not followed.
- **Contents are removed, the directory is not** — apps expect their cache dir to exist.
- **Root work uses `sudo -n`** against exactly the two pinned commands. Without the
  sudoers rule those rows render `AUTH` and `CLEAN NOW` skips them. Never hangs.
- **`[KILL]` is `SIGTERM` only**, armed on first click (button reads `SURE?` for 3 s).
  The widget's own PID is excluded from the list — it cannot restart itself.
- **Threaded.** Scanning and cleaning run on a worker; results arrive via
  `GLib.idle_add`. One target failing marks that row `ERR` and does not abort the rest.

## Keeping apps alive across a clean

Two layers:

1. **Proactive** — a target may declare an owning service. `vicinae` does, so the
   sequence is `systemctl --user stop vicinae` → clear cache → `start`. There is no
   crash to recover from.
2. **Reactive safety net** — `snapshot_watched()` records which watched processes
   (vicinae, conky) are alive before cleaning. 1.5 s after the clean finishes,
   anything that was up and is now down is relaunched with its real command
   (`systemctl --user restart vicinae.service`,
   `setsid conky -c ~/.config/conky/nothing/bg.conf`). The card reports
   `RESTARTED vicinae`.

## Timeline

Sampler ticks every 30 s from the widget instance, which is constructed at startup
like the other rail pop-outs — so it records whether or not the card is open. It reads
`/proc/*/statm` and `/proc/*/comm` directly (no `ps` subprocess), matching how the
`System` card reads `/proc`. Top 8 by RSS per sample append to
`~/.config/nothing-widgets/cleaner-history.jsonl`, pruned to 7 days on write.
Aggregation yields per-app peak RSS and a 12-bucket sparkline.

That file is runtime state, so `sync.sh` excludes it alongside `positions.json`.

## Install changes

- `install.sh` / `sync.sh` copy `cleaner_core.py` alongside `nothing-widgets.py`.
- New `config/nothing-cleaner.sudoers` in the repo.
- `install.sh --with-cleaner-sudo` validates it with `visudo -c` and installs to
  `/etc/sudoers.d/`. Opt-in — a plain install never asks for root.
- README gains a CLEAN row in the widget table and a note on the sudoers step.

## Testing

`nothing-widgets/test_cleaner_core.py`, stdlib `unittest`, no deps, no display:

- path guard rejects paths outside the allowed roots
- path guard rejects a symlink pointing outside the allowed roots
- clean empties a directory's contents but leaves the directory itself
- reported sizes match what was written
- history prunes entries older than 7 days
- aggregation reports the correct per-app peak

Then the widget is deployed live and confirmed to render, scan, and report real
numbers before the change is pushed.
