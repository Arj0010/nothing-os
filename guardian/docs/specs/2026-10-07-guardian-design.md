# Guardian — disk & process manager agent (Phase 1) — Design

**Date:** 2026-10-07 · **Status:** Draft for review · **Owner:** Arjun

## 1. Purpose

Prevent the laptop from reaching an unbootable / frozen state (disk full or RAM exhausted),
which on 2026-10-07 required a GRUB-shell cleanup. Guardian watches disk and processes,
proposes fixes, and **asks before acting**. Security monitoring is Phase 2 (out of scope here).

**Machine:** Linux Mint 22.3, i5-1135G7, 11.5 GB RAM, 8 GB zram + 7.6 GB disk swap,
90 GB root (77% after manual cleanup). Docker with 7 dev containers.

**Already in place (not part of Guardian):** `earlyoom` handles sub-second RAM emergencies
(SIGTERM at mem ≤6% & swap ≤40%). Guardian works on the minutes-scale *before* that.

### Success criteria
- Disk never exceeds 95% for more than ~12 minutes (emergency path).
- Memory hogs / leaks / idle containers are surfaced while there is still time to decide.
- Zero user data loss: no action outside the closed registry ever runs.
- Resident cost between runs: 0 MB (nothing stays running except pending askers).

## 2. Decisions (agreed in brainstorming)

| Topic | Decision |
|---|---|
| Agent model | Rule-based executor + LLM advisor that only *suggests* |
| LLM | `claude -p --model haiku` (Haiku 4.5); offline/failure → non-LLM "top 10 biggest folders" report |
| Asking | Desktop notification with buttons; `guardian review` CLI for longer lists |
| Autonomy | Always ask; **one exception**: disk ≥95% unanswered 10 min → zero-risk list auto-runs |
| Runtime | Python CLI, systemd **user** timer, short-lived runs |
| Order | Disk + processes now; security Phase 2 |

## 3. Cadence

| Check | Interval |
|---|---|
| RAM/swap/top processes, `df` | every 2 min (`guardian.timer`) |
| Deep disk scan (sizes of known junk dirs, Docker reclaimable, big new dirs) | every 6 h, or immediately when disk ≥85% (at most once per 30 min) |
| LLM advisor | only when disk ≥85% after rule findings are resolved/declined, or an unclassified process is a top-3 RSS hog; max 1 call / 6 h |
| Re-ask same finding | at most every 2 h; "Skip" snoozes it 24 h |

## 4. Architecture

```
~/guardian/
  guardian/
    probes/ram.py      # psutil: mem, swap, top-N procs (pid, start_time, name, cmdline, rss)
    probes/disk.py     # df for /, deep scan of allow-listed dirs, top new dirs under ~ (size only)
    probes/docker.py   # docker CLI: per-container mem, CPU-idle duration, reclaimable bytes
    rules.py           # facts -> list[Finding]
    actions.py         # closed registry of Action implementations + safety guards
    advisor.py         # claude -p, JSON-schema output, fallback report
    ask.py             # notification asker + review CLI rendering
    state.py           # SQLite (state.db): findings, decisions, snoozes, history, rss samples
    cli.py             # entrypoints: run, scan, review, act, protect, history
  root-helper/guardian-root-helper   # fixed root script, polkit-allowed
  systemd/guardian.service, guardian.timer
  tests/
```

Each unit only talks to its neighbours through Pydantic models:
`Probe -> Facts -> rules -> Finding -> ask -> Decision -> actions -> HistoryEntry`.

### 4.1 Data models (Pydantic)
- `Finding`: `id` (stable hash of kind+target), `kind`, `severity` (info|warn|critical|emergency),
  `title`, `reason`, `est_saving_bytes`, `action: ActionSpec | None`, `created_at`.
- `ActionSpec`: `name` (registry key), `target` (typed per action), `fingerprint`
  (e.g. pid+start_time+name for processes; container id for containers).
- `Decision`: `finding_id`, `choice` (do|skip|details|timeout), `at`.
- `AdvisorSuggestion`: `path`, `size_bytes`, `what_it_is`, `safe_to_delete_confidence`, `how_to_clean` (text only).

### 4.2 Action registry (the only code that mutates the system)

| Action | Target | Notes |
|---|---|---|
| `kill_process` | pid fingerprint | re-verify pid+start_time+name; refuse if protected; SIGTERM → 10 s → SIGKILL |
| `stop_container` | container id | `docker stop` only, never `rm`; re-check still idle |
| `docker_builder_prune` | — | `docker builder prune -af` |
| `docker_image_prune_dangling` | — | dangling only (`docker image prune -f`) |
| `clear_user_cache` | name ∈ {pip, uv, npm, ms-playwright, thumbnails, trash} | fixed path map; `rm -rf` of that exact path only |
| `apt_clean` | — | via root helper |
| `journal_vacuum` | — | via root helper, `--vacuum-size=200M` |
| `rotated_logs_clean` | — | via root helper, `/var/log/*.gz` and `*.1` older than 7 d |

**Zero-risk list** (emergency auto-run): `docker_builder_prune`, `clear_user_cache` (pip, uv, npm),
`apt_clean`, `journal_vacuum`, `rotated_logs_clean`.

### 4.3 Root helper
`/usr/local/sbin/guardian-root-helper {apt-clean|journal-vacuum|rotated-logs}` — accepts exactly
those three verbs, no other arguments. A polkit rule allows user `arjun` to run it via `pkexec`
without a password. Installed once by `install.sh` (requires one password prompt).

## 5. Rules (Phase 1)

| Finding | Condition | Severity | Action |
|---|---|---|---|
| disk_high | `/` ≥80% | info (daily digest) | — |
| disk_high | ≥85% | warn | best reclaim action(s) from deep scan |
| disk_high | ≥92% | critical | same |
| disk_emergency | ≥95% and critical finding unanswered 10 min | emergency | zero-risk list, auto |
| ram_low | MemAvailable <20% | warn | kill top non-protected hog |
| ram_low | MemAvailable <10% and falling over last 3 samples | critical | same |
| leak_suspect | process RSS grew >1.5 GB within 30 min and still growing | warn | kill_process |
| container_idle | CPU <1% for >6 h (from samples) and mem >100 MB | info (digest) / warn if ram_low | stop_container |
| reclaimable | build cache >2 GB / user cache >1 GB / journal >500 MB | info, or bundled into disk_high | matching action |

Thresholds live as constants in `rules.py` (not user-config in Phase 1).

## 6. Decision flow

1. `guardian.timer` fires `guardian run` every 2 min (oneshot, `Nice=10`, `IOSchedulingClass=idle`).
2. Probes collect facts; rss samples appended to `state.db` (kept 24 h).
3. Rules produce findings; snoozed / recently-asked findings filtered out.
4. For each new warn/critical finding, `run` launches a **detached asker** via
   `systemd-run --user --unit=guardian-ask-<id> guardian ask <id>` and exits.
   The asker runs `notify-send -A do="Do it" -A skip="Skip 24h" -A details="Details"`
   (critical: `-u critical`, no expiry; warn: expires 15 min → `timeout`), records the
   Decision, and on `do` calls `guardian act <id>`. Only one asker per finding id.
5. `details` opens a terminal with `guardian review`, which lists pending findings with full
   reasons and advisor suggestions, and asks y/n per item.
6. `act` re-validates the fingerprint, executes the registry action, writes history, and sends
   a result notification ("Freed 4.1 GB" / "Refused: process changed").
7. Info findings are batched into one digest notification per day (no buttons; points to `guardian review`).

## 7. Advisor

- Input: deep-scan summary (top 30 dirs by size with mtimes, Docker df, top processes) — no file contents.
- Call: `claude -p --model haiku --output-format json` with a prompt requiring JSON matching
  `list[AdvisorSuggestion]`; 60 s timeout.
- Output is validated with Pydantic; invalid → discarded, fallback used.
- Suggestions are **displayed only** in `guardian review`. If a suggestion maps exactly onto a
  registry action (e.g. a known cache name), review may offer that registry action; otherwise text.
- Failure/offline/rate-limited → fallback report (top 10 dirs by size), no retry until next window.

## 8. Safety invariants (hard-coded)

1. Deletion only through `clear_user_cache` fixed path map, Docker prune commands, and root helper verbs. No generic delete.
2. Never touched: Docker volumes, images used by any container, anything else under `~`, git repos, databases.
3. Protected process names (never killed): `cinnamon*`, `Xorg`, `gdm*`, `systemd*`, `sshd`, `dbus-daemon`,
   `pipewire`, `wireplumber`, `tailscaled`, `earlyoom`, `claude`, `guardian`, plus user list via `guardian protect <name>`.
4. Fingerprint re-check before every process/container action.
5. Any exception → log + exit without acting.
6. `guardian scan --dry-run` prints findings and intended actions, executes nothing.
7. Every action and decision recorded in `state.db` history; `guardian history` shows it.

## 9. Testing

- **Unit (pytest, TDD):** rules on synthetic facts (disk 96%, leak curve, idle container);
  actions guards (protected name, recycled pid, unknown cache name refused);
  advisor schema rejection; snooze/re-ask logic; emergency 10-min timer.
- **Integration:** throwaway `alpine` container for stop_container; a dummy python memory-hog for
  leak detection + kill; a loop-mounted small ext4 image filled to 96% to exercise the disk
  path (probes take the mount point as a parameter so tests never touch `/`).
- **E2E (manual, on desktop):** install, run timer, confirm a notification click path and history entry.

## 10. Out of scope (Phase 1)

Security monitoring (exposed ports, new autostarts, auth failures, updates) — Phase 2.
Web dashboard. Config files for thresholds. Multi-machine.

## 11. Install

`~/guardian/install.sh`: create venv (`python3 -m venv`, deps: `psutil`, `pydantic`), install
systemd user units, enable timer; then `pkexec` once to install root helper + polkit rule.
Uninstall script reverses all of it.
