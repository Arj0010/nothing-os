# Adaptive optimizer widget design

Date: 2026-09-14
Status: approved for implementation planning

## Goal

Turn the current CLEAN widget into a compact, safe system controller optimized for
Arjun's normal workload: several Claude and Codex terminal sessions doing heavy
engineering work, Brave open alongside them, VS Code used mainly for reading,
occasional Brave meetings, and Spotify, VLC, or Telegram running in the background.

The controller must improve responsiveness under contention without pretending it
can give one program literally all hardware or bandwidth. CPU, disk I/O, memory
protection, and network preference are contention policies: they determine who wins
when resources are busy. They do not reserve idle capacity or invent bandwidth.

## Scope

The first release contains one built-in profile, `AGENT WORK`. Users can temporarily
override its primary application from the widget, but reusable custom profiles and
additional workload templates are deliberately deferred.

The release includes:

- automatic discovery of every Claude and Codex process tree, across multiple terminals
- four policy modes: Auto, Performance, Battery, and Focus
- optional Deep Focus inside Focus mode
- protected interactive lanes for Brave and VS Code
- protected media/communication lanes for Spotify, VLC, and Telegram
- meeting-aware promotion for Brave
- CPU, disk-I/O, memory-pressure, and network prioritization with honest capability reporting
- exact application-group Pause, Resume, graceful End, and last-resort Force actions
- the existing storage cleaner as a separate nested view

The release does not include multiple saved profiles, automatic cache cleaning,
arbitrary root commands, hard memory limits, automatic process termination, or an
attempt to manage kernel/session services.

## Existing system and problems

The current implementation has a sound storage boundary: risky deletion logic lives
in `nothing-widgets/cleaner_core.py`, while GTK rendering lives in
`nothing-widgets/nothing-widgets.py`. The widget has since accumulated power-profile
and process-killing behavior that is not storage cleaning.

The current memory list aggregates by `/proc/<pid>/comm` and its local group-kill
change signals every process with the same name, escalating from `SIGTERM` to
`SIGKILL`. This is unsuitable for policy enforcement:

- `comm` is truncated to 15 characters and is not a stable application identity.
- unrelated processes can share a name.
- a historical memory row is not an ownership boundary.
- killing a renderer or helper can make a parent immediately recreate it.
- an optimizer must be reversible; process death is not.

The repository currently has unrelated uncommitted edits. Implementation commits
must stage only optimizer/cleaner hunks and new files, leaving desktop layout,
panel, Vicinae, Speed Test, and Now Playing work untouched.

The cleaner tests currently run 57 cases with one failing assertion in
`test_root_rows_report_auth_when_the_rule_is_absent` and two resource warnings from
unclosed read handles. The failure compares all safe reclaimable bytes to the size
of a single locked target, which is not a valid invariant. These baseline defects
will be corrected as part of the touched cleaner surface.

## Product model

### Profile versus mode

`AGENT WORK` defines which applications have which roles. A mode changes the
intensity of the resource policy without changing those roles.

At login the widget starts in Auto, not the last high-power mode. Auto uses the
balanced platform power profile while idle and raises relative weights when a
Claude or Codex tree is active. This avoids silently booting into Performance or
Deep Focus.

The automatic primary role is every live Claude/Codex tree. A manual selection in
`EDIT PRIORITY` becomes the primary role until the user chooses `RESET TO AGENT
WORK`; it never rewrites the built-in profile.

### Application roles

| Role | Default members | Contract |
|---|---|---|
| Primary | Claude, Codex, and their descendants | Wins CPU, I/O, memory-pressure, and network contention. Multiple sessions share the role fairly. |
| Meeting | Brave while capture is active | Highest latency protection; never paused, throttled, or ended automatically. |
| Interactive | Brave and VS Code | Remains responsive; Brave never drops below this role if meeting detection is unavailable. |
| Media/comms | Spotify, VLC, Telegram | Enough CPU/network service for uninterrupted playback and notifications. |
| Background | Other user applications | Lower relative weights; pausable by Deep Focus when safe. |
| Protected | Desktop, session, audio, network, widget, and optimizer guard processes | Never paused, ended, force-killed, or moved by the controller. |

Applications can have a stronger transient role. For example, Brave normally has
Interactive policy and temporarily receives Meeting policy. The strongest current
role always wins.

### Mode behavior

| Mode | Platform profile | Primary | Interactive/media | Background |
|---|---|---|---|---|
| Auto | Balanced; adaptive | High only while agent work is active | Guaranteed responsive | Normal-to-low |
| Performance | Performance when available | Maximum relative weights | High/normal | Low |
| Battery | Power saver | Efficient, nonzero progress | Latency guarantees retained | Strongly reduced |
| Focus | Balanced | Very high | Guaranteed | Strongly reduced |

Deep Focus is a separate switch within Focus. It freezes eligible Background app
groups instead of terminating them. Switching it off resumes the exact groups that
this activation froze.

## Application identity

Policy must operate on an `AppGroup`, never a short process name. The discovery
layer reads only non-content process metadata:

- PID, parent PID, UID, start time, executable identity, and cgroup path from `/proc`
- systemd user application scope when one exists
- desktop application ID and window class supplied by the existing dock/window integration
- parent/descendant relationships rooted at a recognized Claude or Codex executable

Each process identity is `(pid, start_time)`, preventing PID reuse from making a
later process inherit an old action. The controller manages only processes owned by
the current desktop user.

Claude/Codex descendants inherit Primary even when they are Python, Node, Java,
compilers, test runners, or language-specific tools. Classification follows the
ancestor relationship and cgroup membership, not a hard-coded language list.

An application with an unresolvable identity remains visible as unmanaged. The UI
must not offer destructive controls or claim optimized status for it.

## Architecture

### `optimizer_core.py`

A new display-independent module owns:

- typed mode, role, process, app-group, capability, and applied-state models
- `/proc` discovery and ancestry resolution
- application classification and protected-group rules
- meeting/media activity probes
- policy calculation
- application of unprivileged resource controls
- Pause/Resume/End/Force state machines
- atomic settings and restoration journal
- backend capability detection and status reporting

All system interaction is injected behind small runner, process-source,
systemd-source, and clock interfaces so unit tests never manipulate real desktop
applications.

`cleaner_core.py` remains the storage-cleaning module. Its legacy process functions
may remain temporarily for compatibility, but the new UI will not call them and no
optimizer action will be keyed by `comm`.

### Resource backends

The controller composes independent backends and reports each as `full`, `fallback`,
or `unavailable`.

1. **Power backend** uses `powerprofilesctl` when available. Mode transitions save
   the prior profile and restore it when optimization is disabled. Failure changes
   capability status; it does not block the rest of the policy.
2. **CPU/I/O backend** prefers systemd user scopes and cgroup-v2 `CPUWeight` and
   `IOWeight`. It falls back to lowering Background priority with positive `nice`
   and best-effort `ionice`; it never promises a negative nice value without the
   required capability.
3. **Memory backend** uses soft protection/pressure controls where delegated. It
   may protect Primary and Meeting working sets or increase reclaim pressure on
   Background groups, but it never uses `MemoryMax` or another limit that can cause
   an application OOM kill.
4. **Network backend** prefers an optional, narrowly constrained privileged helper.
   It maps validated current-user application cgroups into high, normal, and low
   traffic classes on the active default-route interface. It must probe kernel,
   nftables, and traffic-control support before applying anything. It adds only
   namespaced rules it owns, refuses to replace a pre-existing custom queueing
   policy, and removes only its own rules on restore. If full classification is not
   available, the UI reports `NETWORK · BASIC` and continues without fake per-app
   prioritization.

Weights are relative and apply only under contention. Initial numeric weights live
in one policy table, are covered by tests, and can be tuned from measurements
without changing discovery or UI code. Policy updates use a short cooldown so a
busy/idle transition cannot thrash system controls.

### Privileged helper

Full network priority requires privileges not available to a normal desktop
process. The optional helper is installed root-owned and non-writable by the user.
It accepts a small typed policy document and supports only capability probe, apply,
and restore operations.

Before acting it validates:

- the caller and every target process belong to the active desktop UID
- every cgroup is below that user's systemd hierarchy
- the interface is the current default-route interface
- mode, role, mark, and weight values are from fixed allowlists
- no path, executable, shell fragment, or arbitrary command is supplied
- its namespaced firewall/traffic-control objects do not collide with existing policy

The installer validates and installs the helper separately from the existing cache
cleaner permission. A plain install retains the unprivileged fallback. The widget
never displays `NETWORK · PRIORITIZED` unless a post-apply probe proves the helper's
rules are active.

### Reversible application control

Process control has three levels:

1. **Pause/Resume** is normal and reversible. Prefer systemd unit freeze/thaw for a
   resolved application scope; otherwise signal the exact recorded process group.
2. **End** is manual and double-confirmed. Prefer the desktop/window close protocol,
   then a graceful systemd stop or `SIGTERM` to the exact current group. Recheck
   liveness and report survivors honestly.
3. **Force** appears only after End fails and requires a new confirmation. It may
   send `SIGKILL` only to the still-live `(pid, start_time)` identities in that same
   non-protected group.

Auto, Performance, Battery, Focus, and the cleaner never call End or Force.

A separate pause guard holds the activation lease. If the GTK widget exits or the
control pipe closes, the guard resumes every group frozen by that activation and
restores reversible priorities. Startup also reads the atomic restoration journal
and repairs a prior interrupted session before enabling controls.

### Meeting safety

Brave always retains Interactive policy. Capture detection can only promote it; a
failed probe can never demote it.

The probe uses PipeWire/PulseAudio metadata to detect active microphone or camera
capture. When a capture stream can be attributed to Brave, the controller applies
Meeting policy. If capture is active but attribution is uncertain while Brave is
running, fail safe and promote Brave. The UI displays `MEETING GUARD ARMED` or a
live protected state.

Audio session services such as PipeWire and WirePlumber are Protected. Spotify/VLC
playback remains in the Media role even in Battery or Focus. Deep Focus never
freezes active media or a capture client.

## UI

The permanent right-side card becomes `OPTIMISE ://` while retaining its saved
widget identity/position for compatibility.

The main view contains:

- Auto, Perf, Battery, and Focus mode controls
- `AGENT WORK` with auto-detected Claude/Codex session count
- `EDIT PRIORITY` for a temporary primary override
- Brave, VS Code, and media/communications lane state
- live CPU, network, disk-I/O, and memory capability/allocation summaries
- Meeting Guard state
- Deep Focus switch
- links to `OPEN CLEANER` and `APP CONTROL`

`OPEN CLEANER` replaces the card body with a storage-only view. Safe targets are
selected, expensive-to-rebuild caches remain held, and results are rescanned. It
does not expose process controls.

`APP CONTROL` shows resolved application groups. Protected and Primary groups have
status labels rather than destructive buttons. Eligible groups offer Pause; End is
manual and Force is hidden until needed. The seven-day `comm`-based memory timeline
is no longer a process-control surface.

Every asynchronous action disables only the affected control, shows an in-progress
state, and reports verified results. Failure in one resource backend does not freeze
GTK or roll back unrelated successful backends.

## Persistence and restoration

User settings are stored atomically under `~/.config/nothing-widgets/` and contain
only the selected mode-independent preferences and temporary primary override.
Runtime state is stored separately and includes original power profile, priorities,
managed app identities, helper ownership tokens, and frozen groups.

Auto is the login mode. Performance and Deep Focus do not survive a logout or
crash. Restore is idempotent: it is safe to run during normal shutdown, guard
cleanup, and next startup.

The controller never restores a PID whose start time no longer matches, never
resumes a group it did not freeze, and never deletes network/cgroup objects it did
not create.

## Failure behavior

- Missing `powerprofilesctl`: keep relative controls and show power unavailable.
- User systemd bus unavailable: use nice/ionice and safe signal fallbacks.
- Privileged helper absent or rejected: continue with `NETWORK · BASIC`.
- Meeting probe uncertain: protect Brave.
- Identity uncertain: show unmanaged; do not pause, end, or force.
- Partial policy application: retain successful independent backends, record the
  exact degraded capability, and restore only applied state.
- Widget/guard crash: restoration journal and guard lease thaw paused apps and
  remove owned transient policy.
- Network interface change: restore the old interface's owned policy, probe the new
  default route, and reapply only after validation.

## Files and integration

Expected implementation surface:

- `nothing-widgets/optimizer_core.py` — discovery, policy, backends, safety, state
- `nothing-widgets/test_optimizer_core.py` — display-free unit and contract tests
- `nothing-widgets/optimizer_helper.py` — constrained privileged network helper
- `nothing-widgets/nothing-widgets.py` — three-view GTK controller
- `nothing-widgets/cleaner_core.py` and its tests — only baseline fixes and removal
  from the process-control path
- `config/nothing-optimizer.sudoers` — exact helper authorization, opt-in
- `install.sh`, `sync.sh`, and `bin/verify-desktop` — install/sync/verification
- `README.md` — behavior, safety, capabilities, install and removal

Mockup files under `.superpowers/` are brainstorming state and must not be included
in implementation commits.

## Testing

Development follows test-first behavior around the risky boundaries.

Unit tests use fake `/proc` trees and injected runners to cover:

- multiple Claude/Codex roots and arbitrary-language descendants become Primary
- same-name unrelated processes remain separate AppGroups
- PID reuse is rejected through start-time comparison
- desktop/session/audio/network/widget processes are always Protected
- strongest transient role wins
- each mode produces the intended relative policy
- partial backend failure yields accurate degraded capabilities
- Pause/Resume restores only groups paused by the controller
- End is graceful and Force cannot run without an observed failed End
- no automatic mode can reach End or Force
- guard disconnect and startup recovery are idempotent
- helper schema, UID, cgroup, interface, enum, and ownership validation
- helper refuses existing custom network policy and removes only owned rules
- uncertain meeting attribution promotes Brave
- settings and runtime journals are atomic and tolerate corrupt files

Integration verification includes:

- existing cleaner suite passes without resource warnings
- optimizer suite passes without touching real user applications
- Python byte-compilation of all changed modules
- GTK construction smoke test when a display is available
- power-profile round trip with restoration when permitted
- Pause/Resume and End against uniquely named disposable test processes only
- helper dry-run output before any privileged test
- opt-in network contention test, followed by proof that the original interface
  policy is restored
- live widget check that multiple Claude/Codex sessions are counted and Brave is
  never marked pausable during meeting protection

## Acceptance criteria

The feature is complete when:

1. Auto detects all live Claude/Codex trees and applies Primary policy to their
   descendants without classifying unrelated same-name processes.
2. Switching modes changes and reports available power/resource policy without
   blocking GTK.
3. Brave remains responsive in every mode and receives Meeting policy on capture
   activity or attribution uncertainty.
4. Spotify/VLC playback and Telegram's allowed notification lane continue in Focus
   and Battery.
5. Deep Focus pauses only eligible Background groups and reliably restores them.
6. Cleaner performs storage work only; no automatic action terminates a process.
7. Manual End and Force operate only on a resolved, confirmed, non-protected app
   group and report verified results.
8. Network status distinguishes full prioritization from fallback/basic behavior.
9. Disabling the optimizer or interrupting it restores all controller-owned state.
10. All scoped tests and verification checks pass, and the commit excludes existing
    unrelated working-tree changes and brainstorming mockups.
