# Adaptive Optimizer Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mixed CLEAN/performance/process surface with one safe `AGENT WORK` optimizer that prioritizes all Claude/Codex trees, protects Brave meetings and media, supports reversible Focus controls, and keeps storage cleaning separate.

**Architecture:** Add a display-independent `optimizer_core.py` with typed process identity, role classification, policy calculation, resource backends, and exact application actions. The GTK file renders a three-view card (`OPTIMISE`, `CLEANER`, `APP CONTROL`) and delegates all system decisions to the core; a small guard process restores frozen applications if the widget exits. Per-application network QoS plugs into the backend interface but is implemented by the companion network-helper plan.

**Tech Stack:** Python 3.12 standard library, PyGObject/GTK3, Linux `/proc`, systemd user units/cgroup v2, `powerprofilesctl`, `nice`, `ionice`, stdlib `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-14-adaptive-optimizer-design.md`

## Global Constraints

- Ship one built-in profile named `AGENT WORK`; do not add reusable custom profiles.
- Auto is the login mode. Performance and Deep Focus never persist across login or crash.
- Claude, Codex, and arbitrary-language descendants are Primary; do not classify by a language allowlist.
- Brave never drops below Interactive and is promoted when capture attribution is active or uncertain.
- Spotify, VLC, and Telegram retain their Media/comms lane in Battery and Focus.
- Automatic modes and storage cleaning must never call End, Force, `SIGTERM`, or `SIGKILL`.
- Every process action targets `(pid, start_time)` identities owned by the current UID, never `/proc/comm` alone.
- Hard memory limits such as `MemoryMax` are forbidden.
- System changes are reversible and restore only controller-owned state.
- Use standard-library tests with no display and no third-party Python dependencies.
- The working tree is dirty. Before every commit, inspect `git diff --cached`; never stage desktop-state, panel, Vicinae, Speed Test, Now Playing, or `.superpowers/` changes.
- Do not deploy to live `~/.config`, install privileged files, terminate a real app, or alter a real network interface during automated tests.

## File Structure

- Create `nothing-widgets/optimizer_core.py`: domain models, discovery, classification, policies, capabilities, resource application, state journal, and exact application actions.
- Create `nothing-widgets/optimizer_guard.py`: stdin-driven pause lease that resumes recorded identities when its parent pipe closes.
- Create `nothing-widgets/test_optimizer_core.py`: display-free unit and disposable-process integration tests.
- Modify `nothing-widgets/nothing-widgets.py:1635-2029,2857`: import the core and replace `Cleaner` with the three-view `Optimizer` card while retaining widget name `clean`.
- Modify `nothing-widgets/cleaner_core.py:340-450`: retain detached daemon handles and close power-supply and CPU-info handles correctly; leave storage behavior intact.
- Modify `nothing-widgets/test_cleaner_core.py:559-573`: correct the invalid reclaimable-total assertion and make the resource-warning regression explicit.
- Modify `install.sh:8-10`: deploy optimizer modules with the widget.
- Modify `sync.sh:8-11`: sync optimizer modules and exclude runtime journals.
- Modify `bin/verify-desktop:70-79`: compare optimizer modules between repo and live configuration.
- Modify `README.md:97-131`: document the optimizer modes, process safety, cleaner separation, and fallback capability labels.

---

### Task 1: Repair the touched cleaner baseline

**Files:**
- Modify: `nothing-widgets/cleaner_core.py:340-450`
- Modify: `nothing-widgets/test_cleaner_core.py:267-307,559-573`

**Interfaces:**
- Consumes: existing `on_ac() -> bool`, `cpu_mhz() -> int`, `reclaimable(rows) -> int`
- Produces: the same public signatures with retained detached-process handles, closed file handles, and a host-independent authorization assertion

- [ ] **Step 1: Replace the invalid root-row assertion with the actual invariant**

```python
total = cc.reclaimable(list(rows.values()))
expected = sum(
    row["bytes"]
    for row in rows.values()
    if row["state"] == "ok" and not row["held"]
)
self.assertEqual(total, expected)
self.assertNotIn("apt", cc.auto_keys(list(rows.values())))
self.assertNotIn("journal", cc.auto_keys(list(rows.values())))
```

- [ ] **Step 2: Add warning-as-error coverage for the two readers**

```python
def test_power_readers_do_not_leak_file_handles(self):
    import gc
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        cc.on_ac()
        cc.cpu_mhz()
        gc.collect()
```

Add a detached-spawn lifecycle assertion that patches `subprocess.Popen`, calls
`_spawn`, and proves the returned running handle remains referenced in
`cc._DETACHED_PROCS` until a later `_reap_detached()` observes its exit.

- [ ] **Step 3: Run the cleaner test to verify the baseline defects fail**

Run: `python3 nothing-widgets/test_cleaner_core.py`

Expected: the reclaimable assertion passes after Step 1, while the new warning test fails with an unclosed `/proc/cpuinfo` or power-supply handle.

- [ ] **Step 4: Retain and reap detached daemon handles**

```python
_DETACHED_PROCS = []


def _reap_detached():
    _DETACHED_PROCS[:] = [process for process in _DETACHED_PROCS
                           if process.poll() is None]


def _spawn(cmd):
    try:
        _reap_detached()
        process = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        _DETACHED_PROCS.append(process)
        return True
    except Exception:
        return False
```

- [ ] **Step 5: Use context managers in both readers**

```python
def on_ac():
    try:
        for name in os.listdir("/sys/class/power_supply"):
            if not name.startswith("A"):
                continue
            path = os.path.join("/sys/class/power_supply", name, "online")
            try:
                with open(path) as fh:
                    if fh.read().strip() == "1":
                        return True
            except OSError:
                pass
    except OSError:
        pass
    return False

def cpu_mhz():
    best = 0.0
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.startswith("cpu MHz"):
                    try:
                        best = max(best, float(line.split(":", 1)[1]))
                    except (IndexError, ValueError):
                        pass
    except OSError:
        pass
    return int(best)
```

- [ ] **Step 6: Run the complete cleaner suite with resource warnings promoted**

Run: `python3 -W error::ResourceWarning nothing-widgets/test_cleaner_core.py`

Expected: `OK`; the power-profile round-trip may report one environment-dependent skip.

- [ ] **Step 7: Commit only the cleaner repair hunks**

```bash
git add -p nothing-widgets/cleaner_core.py nothing-widgets/test_cleaner_core.py
git diff --cached --check
git diff --cached -- nothing-widgets/cleaner_core.py nothing-widgets/test_cleaner_core.py
git commit -m "Fix cleaner resource checks"
```

The cached diff must contain only the two context-manager edits, the corrected test invariant, and the new warning test. Do not stage existing optional-root or group-kill edits.

---

### Task 2: Define optimizer domain models and policy table

**Files:**
- Create: `nothing-widgets/optimizer_core.py`
- Create: `nothing-widgets/test_optimizer_core.py`

**Interfaces:**
- Consumes: no earlier optimizer API
- Produces: `Mode`, `Role`, `ProcessIdentity`, `ProcessInfo`, `AppGroup`, `RolePolicy`, `Capability`, `ActionResult`, `OptimizerSettings`, `policy_for(mode, role, active_primary) -> RolePolicy`, `load_settings(path) -> OptimizerSettings`, `save_settings(path, settings) -> bool`

- [ ] **Step 1: Write tests for role ordering and every mode**

```python
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import optimizer_core as oc


class PolicyTest(unittest.TestCase):
    def test_meeting_is_stronger_than_primary_and_interactive(self):
        self.assertGreater(oc.ROLE_STRENGTH[oc.Role.MEETING],
                           oc.ROLE_STRENGTH[oc.Role.PRIMARY])
        self.assertGreater(oc.ROLE_STRENGTH[oc.Role.PRIMARY],
                           oc.ROLE_STRENGTH[oc.Role.INTERACTIVE])

    def test_all_modes_define_every_role(self):
        for mode in oc.Mode:
            for role in oc.Role:
                policy = oc.policy_for(mode, role, active_primary=True)
                self.assertGreaterEqual(policy.cpu_weight, 1)
                self.assertLessEqual(policy.cpu_weight, 10_000)
                self.assertGreaterEqual(policy.io_weight, 1)
                self.assertLessEqual(policy.io_weight, 10_000)

    def test_auto_primary_relaxes_while_agents_are_idle(self):
        busy = oc.policy_for(oc.Mode.AUTO, oc.Role.PRIMARY, True)
        idle = oc.policy_for(oc.Mode.AUTO, oc.Role.PRIMARY, False)
        self.assertGreater(busy.cpu_weight, idle.cpu_weight)
        self.assertGreater(busy.io_weight, idle.io_weight)

    def test_no_policy_contains_an_automatic_termination_action(self):
        for mode in oc.Mode:
            for role in oc.Role:
                self.assertFalse(oc.policy_for(mode, role, True).terminate)
```

- [ ] **Step 2: Run the policy tests to verify the module is missing**

Run: `python3 -m unittest nothing-widgets/test_optimizer_core.py -v`

Expected: ERROR with `ModuleNotFoundError: No module named 'optimizer_core'`.

- [ ] **Step 3: Add immutable enums and dataclasses**

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


class Mode(str, Enum):
    AUTO = "auto"
    PERFORMANCE = "performance"
    BATTERY = "battery"
    FOCUS = "focus"


class Role(str, Enum):
    BACKGROUND = "background"
    MEDIA = "media"
    INTERACTIVE = "interactive"
    PRIMARY = "primary"
    MEETING = "meeting"
    PROTECTED = "protected"


ROLE_STRENGTH = {
    Role.BACKGROUND: 0,
    Role.MEDIA: 1,
    Role.INTERACTIVE: 2,
    Role.PRIMARY: 3,
    Role.MEETING: 4,
    Role.PROTECTED: 5,
}


@dataclass(frozen=True, order=True)
class ProcessIdentity:
    pid: int
    start_time: int


@dataclass(frozen=True)
class ProcessInfo:
    identity: ProcessIdentity
    ppid: int
    uid: int
    comm: str
    exe: str
    cgroup: str
    rss_bytes: int = 0


@dataclass(frozen=True)
class AppGroup:
    key: str
    label: str
    role: Role
    processes: Tuple[ProcessInfo, ...]
    unit: str = ""
    desktop_id: str = ""
    managed: bool = True
    reason: str = ""


@dataclass(frozen=True)
class RolePolicy:
    cpu_weight: int
    io_weight: int
    nice: int
    memory_protection: str
    network_class: str
    terminate: bool = False


@dataclass(frozen=True)
class Capability:
    name: str
    level: str
    detail: str


@dataclass(frozen=True)
class ActionResult:
    action: str
    group_key: str
    changed: int
    survived: int
    protected: bool = False
    detail: str = ""


@dataclass(frozen=True)
class OptimizerSettings:
    manual_primary: str = ""


DEFAULT_LOGIN_MODE = Mode.AUTO
```

- [ ] **Step 4: Add the centralized policy table and accessor**

Use default systemd weights of 100 as the neutral reference:

```python
_POLICIES = {
    Mode.AUTO: {
        Role.PRIMARY: RolePolicy(800, 800, 0, "protected", "high"),
        Role.INTERACTIVE: RolePolicy(300, 250, 0, "normal", "normal"),
        Role.MEDIA: RolePolicy(220, 180, 0, "normal", "normal"),
        Role.BACKGROUND: RolePolicy(60, 50, 5, "reclaimable", "low"),
    },
    Mode.PERFORMANCE: {
        Role.PRIMARY: RolePolicy(2000, 1800, 0, "protected", "high"),
        Role.INTERACTIVE: RolePolicy(500, 400, 0, "normal", "normal"),
        Role.MEDIA: RolePolicy(300, 250, 0, "normal", "normal"),
        Role.BACKGROUND: RolePolicy(40, 40, 8, "reclaimable", "low"),
    },
    Mode.BATTERY: {
        Role.PRIMARY: RolePolicy(350, 300, 1, "protected", "normal"),
        Role.INTERACTIVE: RolePolicy(250, 180, 0, "normal", "normal"),
        Role.MEDIA: RolePolicy(220, 160, 0, "normal", "normal"),
        Role.BACKGROUND: RolePolicy(25, 20, 10, "reclaimable", "low"),
    },
    Mode.FOCUS: {
        Role.PRIMARY: RolePolicy(1400, 1200, 0, "protected", "high"),
        Role.INTERACTIVE: RolePolicy(350, 260, 0, "normal", "normal"),
        Role.MEDIA: RolePolicy(240, 180, 0, "normal", "normal"),
        Role.BACKGROUND: RolePolicy(20, 20, 12, "reclaimable", "low"),
    },
}


def policy_for(mode, role, active_primary=True):
    if role in (Role.MEETING, Role.PROTECTED):
        return RolePolicy(2500, 1000, 0, "protected", "realtime")
    policy = _POLICIES[Mode(mode)][Role(role)]
    if mode == Mode.AUTO and role == Role.PRIMARY and not active_primary:
        return RolePolicy(200, 180, 0, "normal", "normal")
    return policy
```

- [ ] **Step 5: Run the optimizer policy tests**

Run: `python3 -m unittest nothing-widgets/test_optimizer_core.py -v`

Expected: all `PolicyTest` cases pass.

- [ ] **Step 6: Add atomic settings tests and implementation**

```python
class SettingsTest(unittest.TestCase):
    def test_settings_round_trip_only_the_manual_primary(self):
        path = os.path.join(self.tmp.name, "optimizer-settings.json")
        settings = oc.OptimizerSettings(manual_primary="org.gnome.Terminal")
        self.assertTrue(oc.save_settings(path, settings))
        self.assertEqual(oc.load_settings(path), settings)

    def test_corrupt_settings_start_with_agent_work_defaults(self):
        path = os.path.join(self.tmp.name, "optimizer-settings.json")
        with open(path, "w") as fh:
            fh.write("not json")
        self.assertEqual(oc.load_settings(path), oc.OptimizerSettings())
        self.assertEqual(oc.Mode.AUTO, oc.DEFAULT_LOGIN_MODE)
```

`OptimizerSettings` contains only `manual_primary: str = ""`. Save a versioned JSON
object through a mode-`0600` temporary file, `flush`, `os.fsync`, and `os.replace`.
Do not persist the active mode or Deep Focus state.

- [ ] **Step 7: Run policy and settings tests**

Run: `python3 -m unittest nothing-widgets/test_optimizer_core.py -v`

Expected: all policy and settings cases pass.

- [ ] **Step 8: Commit the domain layer**

```bash
git add nothing-widgets/optimizer_core.py nothing-widgets/test_optimizer_core.py
git diff --cached --check
git commit -m "Add optimizer policy model"
```

---

### Task 3: Discover exact process trees and classify application groups

**Files:**
- Modify: `nothing-widgets/optimizer_core.py`
- Modify: `nothing-widgets/test_optimizer_core.py`

**Interfaces:**
- Consumes: `ProcessIdentity`, `ProcessInfo`, `AppGroup`, `Role`
- Produces: `read_processes(proc_root="/proc", uid=os.getuid()) -> tuple[ProcessInfo, ...]`, `classify_groups(processes, desktop_apps=(), capture=None, manual_primary="", uid=os.getuid()) -> tuple[AppGroup, ...]`, `same_process(identity, proc_root="/proc", source=None) -> bool`

- [ ] **Step 1: Add fixture-driven discovery and classification tests**

```python
class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.uid = 1000
        self.procs = (
            proc(10, 1, "claude", "/usr/bin/claude", "/user/app/term-a", self.uid),
            proc(11, 10, "python", "/usr/bin/python3", "/user/app/term-a", self.uid),
            proc(20, 1, "codex", "/usr/bin/codex", "/user/app/term-b", self.uid),
            proc(21, 20, "java", "/usr/bin/java", "/user/app/term-b", self.uid),
            proc(30, 1, "brave", "/usr/bin/brave-browser", "/user/app/brave", self.uid),
            proc(40, 1, "code", "/usr/bin/code", "/user/app/code", self.uid),
            proc(50, 1, "python", "/usr/bin/python3", "/user/app/unrelated", self.uid),
        )

    def test_all_agent_descendants_are_primary_regardless_of_language(self):
        groups = oc.classify_groups(self.procs)
        primary_pids = {
            p.identity.pid for g in groups if g.role == oc.Role.PRIMARY
            for p in g.processes
        }
        self.assertEqual(primary_pids, {10, 11, 20, 21})

    def test_same_comm_outside_agent_tree_is_not_primary(self):
        groups = oc.classify_groups(self.procs)
        unrelated = next(g for g in groups if any(p.identity.pid == 50 for p in g.processes))
        self.assertEqual(unrelated.role, oc.Role.BACKGROUND)

    def test_brave_and_code_have_interactive_roles(self):
        roles = {g.key: g.role for g in oc.classify_groups(self.procs)}
        self.assertEqual(roles["brave"], oc.Role.INTERACTIVE)
        self.assertEqual(roles["code"], oc.Role.INTERACTIVE)

    def test_manual_primary_override_is_stronger_than_default_interactive(self):
        groups = oc.classify_groups(self.procs, manual_primary="code")
        code = next(group for group in groups if group.key == "code")
        self.assertEqual(code.role, oc.Role.PRIMARY)
```

Define the test helper with explicit start times:

```python
def proc(pid, ppid, comm, exe, cgroup, uid=1000, start=None):
    return oc.ProcessInfo(
        identity=oc.ProcessIdentity(pid, start if start is not None else pid * 100),
        ppid=ppid, uid=uid, comm=comm, exe=exe, cgroup=cgroup,
    )
```

Also define `FakeProcessSource(start_times)` with
`start_time(pid) -> int | None`. Define `brave_groups()` as a one-element tuple
containing a managed `AppGroup(key="brave", label="Brave",
role=Role.INTERACTIVE,
processes=(proc(30, 1, "brave", "/usr/bin/brave-browser",
"/user/app/brave"),), unit="app-brave.scope")`.

- [ ] **Step 2: Run the discovery tests and verify missing APIs fail**

Run: `python3 -m unittest nothing-widgets.test_optimizer_core.DiscoveryTest -v`

Expected: ERROR because `classify_groups` is not defined.

- [ ] **Step 3: Implement robust `/proc` parsing**

Read `/proc/$pid/stat` by locating the final `)` before indexing fields; parse field
22 as start time. Read `status` for real UID, `exe` with `os.readlink`, `cgroup` for
the unified `0::` entry, `statm` for RSS, and `comm` for display only. Every file
uses a context manager. Skip a PID if stat, status, or ownership cannot be proven.

```python
def _parse_stat(raw):
    close = raw.rfind(")")
    fields = raw[close + 2:].split()
    return int(fields[1]), int(fields[19])  # ppid, start_time


def descendants(processes, roots):
    children = {}
    for process in processes:
        children.setdefault(process.ppid, []).append(process)
    found = set(roots)
    stack = list(roots)
    while stack:
        parent = stack.pop()
        for child in children.get(parent, ()):
            if child.identity.pid not in found:
                found.add(child.identity.pid)
                stack.append(child.identity.pid)
    return found
```

- [ ] **Step 4: Implement role classification and protected identities**

Use executable basenames for agent roots and known desktop apps. Group agent roots
separately by root PID so multiple sessions share the Primary role without becoming
one destructive target. Group desktop applications by systemd cgroup when present.

```python
AGENT_EXECUTABLES = frozenset(("claude", "codex"))
INTERACTIVE_EXECUTABLES = {"brave": "Brave", "brave-browser": "Brave", "code": "VS Code"}
MEDIA_EXECUTABLES = {"spotify": "Spotify", "vlc": "VLC", "telegram-desktop": "Telegram", "Telegram": "Telegram"}
PROTECTED_EXECUTABLES = frozenset((
    "systemd", "cinnamon", "cinnamon-session", "muffin", "Xorg", "Xwayland",
    "pipewire", "wireplumber", "NetworkManager", "dbus-daemon",
    "nothing-widgets.py", "optimizer_guard.py",
))
```

When a process has no stable cgroup or ancestor root, create an unmanaged singleton
group with `managed=False`; do not merge it by `comm`.

- [ ] **Step 5: Add PID-reuse and UID-boundary tests**

```python
def test_same_process_rejects_reused_pid(self):
    source = FakeProcessSource({77: 9002})
    self.assertFalse(oc.same_process(oc.ProcessIdentity(77, 9001), source=source))

def test_other_users_are_not_returned(self):
    rows = self.procs + (proc(90, 1, "claude", "/usr/bin/claude", "/other", 2000),)
    managed = [p for g in oc.classify_groups(rows, uid=1000) for p in g.processes]
    self.assertNotIn(90, {p.identity.pid for p in managed})
```

- [ ] **Step 6: Run the discovery suite**

Run: `python3 -m unittest nothing-widgets/test_optimizer_core.py -v`

Expected: all policy and discovery cases pass.

- [ ] **Step 7: Commit exact application discovery**

```bash
git add nothing-widgets/optimizer_core.py nothing-widgets/test_optimizer_core.py
git diff --cached --check
git commit -m "Discover optimizer application groups"
```

---

### Task 4: Add meeting-safe role promotion and honest capabilities

**Files:**
- Modify: `nothing-widgets/optimizer_core.py`
- Modify: `nothing-widgets/test_optimizer_core.py`

**Interfaces:**
- Consumes: `AppGroup`, `Role`, `Capability`, injected command runner
- Produces: `CaptureState`, `capture_state(runner) -> CaptureState`, `promote_capture(groups, state) -> tuple[AppGroup, ...]`, `detect_capabilities(runner) -> tuple[Capability, ...]`

- [ ] **Step 1: Write tests for attributed, uncertain, and absent capture**

```python
class CaptureTest(unittest.TestCase):
    def test_attributed_brave_capture_promotes_brave(self):
        state = oc.CaptureState(active=True, brave=True, certain=True)
        groups = oc.promote_capture(brave_groups(), state)
        self.assertEqual(groups[0].role, oc.Role.MEETING)

    def test_uncertain_capture_fails_safe_when_brave_is_running(self):
        state = oc.CaptureState(active=True, brave=False, certain=False)
        groups = oc.promote_capture(brave_groups(), state)
        self.assertEqual(groups[0].role, oc.Role.MEETING)

    def test_probe_failure_never_demotes_brave_below_interactive(self):
        state = oc.CaptureState(active=False, brave=False, certain=False)
        groups = oc.promote_capture(brave_groups(), state)
        self.assertEqual(groups[0].role, oc.Role.INTERACTIVE)
```

- [ ] **Step 2: Run the capture tests and verify they fail**

Run: `python3 -m unittest nothing-widgets.test_optimizer_core.CaptureTest -v`

Expected: ERROR because `CaptureState` is not defined.

- [ ] **Step 3: Parse capture metadata without inspecting media content**

Call `pactl -f json list source-outputs` first and parse `properties` fields such as
`application.process.binary` and `application.name`. If that command fails, call
`pw-dump` and look only at node properties for capture/media class and application
identity. Do not record device names, stream titles, or payload data.

```python
@dataclass(frozen=True)
class CaptureState:
    active: bool
    brave: bool
    certain: bool


def promote_capture(groups, state):
    result = []
    for group in groups:
        role = group.role
        is_brave = group.key.startswith("brave")
        if is_brave and state.active and (state.brave or not state.certain):
            role = Role.MEETING
        elif is_brave and ROLE_STRENGTH[role] < ROLE_STRENGTH[Role.INTERACTIVE]:
            role = Role.INTERACTIVE
        result.append(dataclasses.replace(group, role=role))
    return tuple(result)
```

- [ ] **Step 4: Add capability tests and implementation**

```python
def test_network_capability_is_basic_without_helper(self):
    caps = {cap.name: cap for cap in oc.detect_capabilities(FakeRunner(missing={"nothing-optimizer-helper"}))}
    self.assertEqual(caps["network"].level, "fallback")
    self.assertIn("basic", caps["network"].detail.lower())

def test_power_failure_is_reported_not_raised(self):
    caps = {cap.name: cap for cap in oc.detect_capabilities(FakeRunner(fail={"powerprofilesctl"}))}
    self.assertEqual(caps["power"].level, "unavailable")
```

Capabilities use only `full`, `fallback`, or `unavailable`. Network remains
`fallback / BASIC` until the companion network-helper plan proves its active rules.

- [ ] **Step 5: Run all optimizer tests**

Run: `python3 -m unittest nothing-widgets/test_optimizer_core.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit meeting protection and capabilities**

```bash
git add nothing-widgets/optimizer_core.py nothing-widgets/test_optimizer_core.py
git diff --cached --check
git commit -m "Protect meetings in optimizer policy"
```

---

### Task 5: Apply and restore power, CPU, I/O, and memory policy

**Files:**
- Modify: `nothing-widgets/optimizer_core.py`
- Modify: `nothing-widgets/test_optimizer_core.py`

**Interfaces:**
- Consumes: `Mode`, `AppGroup`, `RolePolicy`, `Capability`, `policy_for`
- Produces: `AppliedGroupState`, `AppliedState`, `NetworkBackend`, `BasicNetworkBackend`, `ResourceController.apply(mode, groups, active_primary) -> AppliedState`, `ResourceController.restore() -> tuple[Capability, ...]`

- [ ] **Step 1: Write runner-based resource application tests**

```python
class ResourceControllerTest(unittest.TestCase):
    def test_performance_requests_platform_performance_profile(self):
        runner = FakeRunner(outputs={("powerprofilesctl", "get"): "balanced"})
        controller = oc.ResourceController(runner=runner, uid=1000)
        controller.apply(oc.Mode.PERFORMANCE, sample_groups(), True)
        self.assertIn(("powerprofilesctl", "set", "performance"), runner.commands)

    def test_background_fallback_is_only_deprioritized(self):
        runner = FakeRunner(fail_prefixes={("systemctl", "--user", "set-property")})
        controller = oc.ResourceController(runner=runner, uid=1000)
        controller.apply(oc.Mode.FOCUS, sample_groups(), True)
        self.assertTrue(any(command[:2] == ("renice", "-n") for command in runner.commands))
        self.assertFalse(any("SIGTERM" in command or "SIGKILL" in command for command in runner.commands))

    def test_restore_returns_the_original_power_profile(self):
        runner = FakeRunner(outputs={("powerprofilesctl", "get"): "balanced"})
        controller = oc.ResourceController(runner=runner, uid=1000)
        controller.apply(oc.Mode.BATTERY, sample_groups(), True)
        controller.restore()
        self.assertEqual(runner.commands[-1], ("powerprofilesctl", "set", "balanced"))
```

Define `FakeRunner` in the test file with `run(command, input_text="")` that records
immutable command tuples, returns configured `(ok, stdout)` pairs, and prefix-matches
configured failures. Define `sample_groups()` as one Primary Claude group with unit
`app-agent.scope` and one Background Postman group with unit `app-postman.scope`.

- [ ] **Step 2: Run the resource tests and verify the controller is absent**

Run: `python3 -m unittest nothing-widgets.test_optimizer_core.ResourceControllerTest -v`

Expected: ERROR because `ResourceController` is not defined.

- [ ] **Step 3: Implement platform-profile transitions**

Map Performance to `performance`, Battery to `power-saver`, and Auto/Focus to
`balanced`. Save `powerprofilesctl get` once per activation. Treat an unavailable
profile as a capability downgrade and continue.

```python
POWER_PROFILE = {
    Mode.AUTO: "balanced",
    Mode.PERFORMANCE: "performance",
    Mode.BATTERY: "power-saver",
    Mode.FOCUS: "balanced",
}
```

Define a `NetworkBackend` protocol with `apply(groups, policies) -> Capability` and
`restore() -> Capability`. `BasicNetworkBackend` performs no mutation and returns
`Capability("network", "fallback", "NETWORK · BASIC")`. Inject it into
`ResourceController`; the companion network plan replaces it when the privileged
helper proves available.

Add the applied-state types before the controller:

```python
@dataclass(frozen=True)
class AppliedGroupState:
    group_key: str
    unit: str
    identities: Tuple[ProcessIdentity, ...]
    backend: str


@dataclass(frozen=True)
class AppliedState:
    mode: Mode
    groups: Tuple[AppliedGroupState, ...]
    capabilities: Tuple[Capability, ...]
```

- [ ] **Step 4: Implement cgroup-v2/systemd user properties**

For a managed group with a systemd unit, call argument-list subprocesses only:

```python
runner.run((
    "systemctl", "--user", "set-property", "--runtime", group.unit,
    "CPUWeight=%d" % policy.cpu_weight,
    "IOWeight=%d" % policy.io_weight,
    "MemoryLow=%s" % ("256M" if policy.memory_protection == "protected" else "0"),
))
```

Query and journal the original properties before the first write. Do not set
`MemoryMax`, `MemoryHigh`, `AllowedCPUs`, scheduler real-time policy, or negative
nice values.

- [ ] **Step 5: Implement per-process fallback and identity recheck**

For each current `(pid, start_time)` in a group, re-read start time immediately
before running `renice -n $nice_value -p $pid` and
`ionice -c 2 -n $io_priority -p $pid`, where `$nice_value` is `0..19` and
`$io_priority` is `0..7`. Skip identities that changed. Journal only successful changes and restore
their original values when readable.

- [ ] **Step 6: Add partial-failure and idempotent-restore tests**

```python
def test_partial_backend_failure_keeps_successful_capabilities(self):
    runner = FakeRunner(fail_prefixes={("systemctl", "--user", "set-property")})
    state = oc.ResourceController(runner, uid=1000).apply(
        oc.Mode.AUTO, sample_groups(), True)
    levels = {cap.name: cap.level for cap in state.capabilities}
    self.assertEqual(levels["cpu_io"], "fallback")
    self.assertIn(levels["power"], ("full", "unavailable"))

def test_restore_twice_is_safe(self):
    controller = oc.ResourceController(FakeRunner(), uid=1000)
    controller.apply(oc.Mode.FOCUS, sample_groups(), True)
    controller.restore()
    controller.restore()
```

- [ ] **Step 7: Run the full optimizer suite**

Run: `python3 -m unittest nothing-widgets/test_optimizer_core.py -v`

Expected: all cases pass; no real power profile or priority changes occur because tests inject `FakeRunner`.

- [ ] **Step 8: Commit reversible resource control**

```bash
git add nothing-widgets/optimizer_core.py nothing-widgets/test_optimizer_core.py
git diff --cached --check
git commit -m "Apply reversible optimizer policy"
```

---

### Task 6: Implement Pause, End, Force, and crash restoration

**Files:**
- Create: `nothing-widgets/optimizer_guard.py`
- Modify: `nothing-widgets/optimizer_core.py`
- Modify: `nothing-widgets/test_optimizer_core.py`

**Interfaces:**
- Consumes: `AppGroup`, `ProcessIdentity`, `ActionResult`, `same_process`
- Produces: `AppController.pause(group)`, `resume(group_key)`, `set_deep_focus(groups, enabled)`, `end(group)`, `force(group, end_token)`, `restore_interrupted()`, `GuardLease`

- [ ] **Step 1: Write safety-state-machine tests**

```python
class AppControllerTest(unittest.TestCase):
    def test_protected_group_cannot_be_paused_or_ended(self):
        controller = controller_with_fakes()
        group = group_with_role(oc.Role.PROTECTED)
        self.assertTrue(controller.pause(group).protected)
        self.assertTrue(controller.end(group).protected)
        self.assertEqual(controller.runner.commands, [])

    def test_force_requires_a_failed_end_token_for_the_same_group(self):
        controller = controller_with_fakes()
        group = group_with_role(oc.Role.BACKGROUND)
        result = controller.force(group, end_token="unissued")
        self.assertEqual(result.changed, 0)
        self.assertIn("graceful end", result.detail.lower())

    def test_pause_records_only_identities_it_changed(self):
        controller = controller_with_fakes()
        group = group_with_role(oc.Role.BACKGROUND)
        result = controller.pause(group)
        self.assertEqual(result.changed, len(group.processes))
        self.assertEqual(set(controller.paused), {group.key})

    def test_modes_have_no_reference_to_end_or_force(self):
        source = inspect.getsource(oc.ResourceController.apply)
        self.assertNotIn(".end(", source)
        self.assertNotIn(".force(", source)

    def test_deep_focus_pauses_only_background_groups(self):
        controller = controller_with_fakes()
        groups = tuple(group_with_role(role) for role in oc.Role)
        controller.set_deep_focus(groups, True)
        self.assertEqual(
            {key for key in controller.paused},
            {group.key for group in groups if group.role == oc.Role.BACKGROUND},
        )
```

Define `group_with_role(role)` as a managed group with a unique key, one fake
`ProcessIdentity`, and a user app scope. Define `controller_with_fakes()` with the
Task 5 `FakeRunner`, a fake process source whose start times match those identities,
an in-memory journal, and a signal callback that only records `(identity, signal)`.

- [ ] **Step 2: Run the action tests and verify missing APIs fail**

Run: `python3 -m unittest nothing-widgets.test_optimizer_core.AppControllerTest -v`

Expected: ERROR because `AppController` is not defined.

- [ ] **Step 3: Implement atomic runtime journal helpers**

Use `optimizer-runtime.json` under `~/.config/nothing-widgets/`. Write mode `0600`
to `path + ".tmp"`, `flush`, `os.fsync`, then `os.replace`. The journal records
only action name, group key, `(pid, start_time)` pairs, unit, and controller-owned
original state. Corrupt input returns an empty journal and an error capability.

- [ ] **Step 4: Implement Pause and Resume**

Prefer `systemctl --user freeze "$unit"` and `thaw "$unit"` for an exact managed user
unit. Otherwise recheck each identity and call injected `send_signal(identity,
signal.SIGSTOP)` or `SIGCONT`. Resume only identities recorded by this controller.

```python
def _action_allowed(group):
    return group.managed and group.role not in (
        Role.PRIMARY, Role.MEETING, Role.PROTECTED,
    )
```

Media and Interactive groups can be manually paused from App Control, but Deep
Focus calls Pause only for Background groups.

`set_deep_focus(groups, False)` resumes only the keys recorded by that Deep Focus
activation, leaving manually paused groups unchanged.

- [ ] **Step 5: Implement graceful End and gated Force**

End first calls an injected desktop close callback for known windows. If unavailable,
use `systemctl --user stop "$unit"` or `SIGTERM` after identity recheck. Wait up to
three seconds through an injected clock and return exact survivors. When survivors
remain, issue a random `secrets.token_urlsafe(24)` bound to the group key and live
identities. Force validates that token and rechecks identities before `SIGKILL`.

- [ ] **Step 6: Implement the pause guard protocol**

`GuardLease` starts `optimizer_guard.py` with a pipe on stdin and writes one JSON
line per paused identity. The guard handles only `track`, `untrack`, and `close`
messages. EOF without `close` sends `SIGCONT` after matching `/proc/$pid/stat`
start time, then calls `optimizer_core.restore_from_journal()` to restore recorded
user-unit and per-process resource properties. A clean close performs the same
idempotent restoration before exit. The companion network plan extends the journal
restore path to call the exact privileged-helper `restore` command when its owned
network state was active.

```json
{"op":"track","pid":4312,"start_time":882001}
{"op":"untrack","pid":4312,"start_time":882001}
{"op":"close"}
```

- [ ] **Step 7: Add a disposable-process guard integration test**

Spawn a uniquely named `sleep 30`, obtain its start time, start the guard, send a
track message, `SIGSTOP` the sleep process, then close the guard's stdin without a
clean-close message. Assert within two seconds that `/proc/$pid/stat` no longer
reports state `T`. Kill and reap the disposable child in `addCleanup`; never signal
an existing process.

- [ ] **Step 8: Run action and guard tests**

Run: `python3 -m unittest nothing-widgets/test_optimizer_core.py -v`

Expected: all tests pass, including the disposable pause-lease recovery.

- [ ] **Step 9: Commit exact application control**

```bash
git add nothing-widgets/optimizer_core.py nothing-widgets/optimizer_guard.py nothing-widgets/test_optimizer_core.py
git diff --cached --check
git commit -m "Add safe optimizer app controls"
```

---

### Task 7: Replace the GTK Cleaner card with the three-view Optimizer

**Files:**
- Modify: `nothing-widgets/nothing-widgets.py:1635-2029,2857`
- Modify: `nothing-widgets/test_optimizer_core.py`

**Interfaces:**
- Consumes: `Mode`, `AppGroup`, `Capability`, existing `cleaner_core` scan/clean APIs
- Produces: `OptimizerController`, `OptimizerSnapshot`, `build_snapshot(mode, groups, capabilities, deep_focus, meeting_active=False) -> OptimizerSnapshot`, and `Optimizer(Widget)` retaining `wname="clean"`; main, cleaner, and app-control views

- [ ] **Step 1: Add a presentation-model contract test before GTK work**

Keep GTK out of unit tests. Add `OptimizerSnapshot` and test its labels:

```python
def test_snapshot_reports_agent_sessions_and_basic_network(self):
    snapshot = oc.build_snapshot(
        mode=oc.Mode.AUTO,
        groups=sample_groups(agent_sessions=4),
        capabilities=(oc.Capability("network", "fallback", "BASIC"),),
        deep_focus=False,
    )
    self.assertEqual(snapshot.profile, "AGENT WORK")
    self.assertEqual(snapshot.agent_sessions, 4)
    self.assertEqual(snapshot.network_label, "NETWORK · BASIC")
    self.assertFalse(snapshot.deep_focus)
```

- [ ] **Step 2: Run the snapshot test and verify it fails**

Run: `python3 -m unittest nothing-widgets.test_optimizer_core.SnapshotTest -v`

Expected: ERROR because `build_snapshot` is not defined.

- [ ] **Step 3: Implement `OptimizerController.tick()` and `build_snapshot()`**

`tick()` discovers processes, classifies roles, applies meeting promotion, calculates
agent activity, applies a policy only when the desired state changes or cooldown
expires, and returns an immutable snapshot. It does not call GTK.

```python
@dataclass(frozen=True)
class OptimizerSnapshot:
    mode: Mode
    profile: str
    agent_sessions: int
    groups: Tuple[AppGroup, ...]
    capabilities: Tuple[Capability, ...]
    network_label: str
    meeting_active: bool
    deep_focus: bool
```

- [ ] **Step 4: Import `optimizer_core` with the existing graceful fallback pattern**

```python
try:
    import cleaner_core
except Exception:
    cleaner_core = None

try:
    import optimizer_core
except Exception:
    optimizer_core = None
```

- [ ] **Step 5: Build the main `OPTIMISE ://` view**

Rename the Python class to `Optimizer` but call `super().__init__("clean", 1440,
270, 430)` so saved placement survives. Use a `Gtk.Stack` for `main`, `cleaner`,
and `apps`. The main view contains:

- four equal mode buttons labeled `AUTO`, `PERF`, `BATTERY`, `FOCUS`
- profile row `AGENT WORK` and `EDIT PRIORITY`
- Primary row `CLAUDE + CODEX TERMINALS` with live session count
- Brave, VS Code, and Spotify/VLC/Telegram lane rows
- CPU, Network, Disk I/O, and Memory capability rows
- Meeting Guard state and Deep Focus switch
- `OPEN CLEANER` and `APP CONTROL` navigation buttons

Mode buttons call a single `_set_mode(mode)` worker. Only the selected button has
class `on`. Deep Focus is sensitive only in Focus mode.

`EDIT PRIORITY` opens a `Gtk.Popover` listing current managed, non-Protected app
groups plus `AUTO · CLAUDE + CODEX`. Selecting a group saves its stable group key as
`OptimizerSettings.manual_primary`, asks the controller to reclassify immediately,
and closes the popover. Selecting Auto clears the override. If a saved key is not
currently running, the UI shows it as unavailable and falls back to Agent Work
without deleting the preference.

- [ ] **Step 6: Move existing storage rendering into the Cleaner stack page**

Reuse `scan`, `reclaimable`, `auto_keys`, held targets, path guards, worker threads,
and result verification. Preserve the current optional `/data` behavior if present
in the working tree. Remove PERF and memory-process controls from this page. The
footer text must include `no processes killed`.

- [ ] **Step 7: Build the App Control stack page**

Render one row per resolved `AppGroup`, not per history name. Primary, Meeting, and
Protected rows show status labels. Eligible rows expose Pause/Resume and End. End
uses the existing three-second double-click confirmation helper. Display Force only
when `ActionResult.survived > 0` and an end token exists; Force gets its own fresh
confirmation.

- [ ] **Step 8: Add the nonblocking controller loop**

Every three seconds, if no tick is active, run `controller.tick()` in a daemon
thread and deliver the immutable snapshot with `GLib.idle_add`. Cleaner scans keep
their existing 60-second schedule. Mode/action buttons disable only while their own
worker is active. On GTK destroy, call `controller.restore()` and close the guard.

- [ ] **Step 9: Replace the constructor call and byte-compile**

Change the bottom-level construction from `Cleaner()` to `Optimizer()`.

Run: `python3 -m py_compile nothing-widgets/nothing-widgets.py nothing-widgets/cleaner_core.py nothing-widgets/optimizer_core.py nothing-widgets/optimizer_guard.py`

Expected: exit 0 with no output.

- [ ] **Step 10: Run display-free suites**

Run: `python3 -W error::ResourceWarning nothing-widgets/test_cleaner_core.py`

Expected: `OK` with only documented environment skips.

Run: `python3 -W error::ResourceWarning nothing-widgets/test_optimizer_core.py`

Expected: `OK`.

- [ ] **Step 11: Inspect and commit only optimizer UI hunks**

```bash
git add -p nothing-widgets/nothing-widgets.py
git add nothing-widgets/optimizer_core.py nothing-widgets/test_optimizer_core.py
git diff --cached --check
git diff --cached -- nothing-widgets/nothing-widgets.py
git commit -m "Build adaptive optimizer widget"
```

Reject hunks for Speed Test, Now Playing, pane layout, or other pre-existing edits.
The staged GTK diff must be limited to imports, the former Cleaner class region,
and the bottom constructor call.

---

### Task 8: Install, synchronize, verify, and document the core optimizer

**Files:**
- Modify: `install.sh:8-10`
- Modify: `sync.sh:8-11,16-18`
- Modify: `bin/verify-desktop:70-79`
- Modify: `README.md:97-131`

**Interfaces:**
- Consumes: `optimizer_core.py`, `optimizer_guard.py`, three-view GTK widget
- Produces: repo-to-live deployment and drift checks for both new modules; user-facing operating/safety documentation

- [ ] **Step 1: Add install and sync copy operations**

```bash
cp "$R/nothing-widgets/optimizer_core.py" ~/.config/nothing-widgets/
cp "$R/nothing-widgets/optimizer_guard.py" ~/.config/nothing-widgets/
```

Add both lines after `cleaner_core.py` in `install.sh` and `sync.sh`. Extend sync's
runtime cleanup with `optimizer-runtime.json`, `optimizer-settings.json`, and their
`.tmp` files so runtime state cannot enter the repository.

- [ ] **Step 2: Add live/repo drift pairs**

```bash
"$HOME/.config/nothing-widgets/optimizer_core.py:$R/nothing-widgets/optimizer_core.py" \
"$HOME/.config/nothing-widgets/optimizer_guard.py:$R/nothing-widgets/optimizer_guard.py" \
```

- [ ] **Step 3: Rewrite the CLEAN README section as OPTIMISE**

Document the `AGENT WORK` role membership, four modes, temporary primary override,
Meeting Guard fail-safe behavior, Deep Focus restoration, App Control action levels,
separate storage Cleaner, and `NETWORK · BASIC` meaning. State explicitly that no
automatic mode terminates applications and that full per-app egress QoS comes from
the optional companion helper plan.

- [ ] **Step 4: Run shell syntax and repository tests**

Run: `bash -n install.sh sync.sh bin/verify-desktop`

Expected: exit 0.

Run: `python3 -W error::ResourceWarning nothing-widgets/test_cleaner_core.py && python3 -W error::ResourceWarning nothing-widgets/test_optimizer_core.py`

Expected: both suites report `OK`.

- [ ] **Step 5: Commit deployment and documentation**

```bash
git add install.sh sync.sh bin/verify-desktop README.md
git diff --cached --check
git diff --cached --stat
git commit -m "Document and install optimizer widget"
```

---

### Task 9: Verify the core feature end to end without touching real applications

**Files:**
- Modify only if verification exposes a scoped defect: optimizer or cleaner files from Tasks 1-8

**Interfaces:**
- Consumes: complete core optimizer
- Produces: recorded verification evidence and a clean, scoped commit series

- [ ] **Step 1: Run all display-free verification from a clean Python process**

Run: `python3 -W error::ResourceWarning nothing-widgets/test_cleaner_core.py`

Expected: `OK`.

Run: `python3 -W error::ResourceWarning nothing-widgets/test_optimizer_core.py`

Expected: `OK`.

Run: `python3 -m py_compile nothing-widgets/nothing-widgets.py nothing-widgets/cleaner_core.py nothing-widgets/optimizer_core.py nothing-widgets/optimizer_guard.py`

Expected: exit 0 with no output.

- [ ] **Step 2: Run controller discovery in dry-run mode**

Add a `--dry-run` entry point to `optimizer_core.py` that prints JSON containing
mode, role labels, PID counts, and capabilities, but no command lines. It must use a
runner whose mutation methods raise if called.

Run: `python3 nothing-widgets/optimizer_core.py --dry-run`

Expected: valid JSON; any live Claude/Codex processes appear as Primary, and network
reports Basic until the companion helper is installed.

- [ ] **Step 3: Test actions against disposable processes only**

Run the named disposable-process integration cases:

```bash
python3 -m unittest \
  nothing-widgets.test_optimizer_core.GuardIntegrationTest \
  nothing-widgets.test_optimizer_core.DisposableActionIntegrationTest -v
```

Expected: Pause/Resume, guard recovery, graceful End, and gated Force pass; cleanup
leaves no test process running.

- [ ] **Step 4: Run a GTK smoke test when a display is available**

Run: `timeout 8s python3 nothing-widgets/nothing-widgets.py`

Expected: the process starts without a traceback. A timeout exit is acceptable; do
not deploy over the live widget or kill the existing widget process.

- [ ] **Step 5: Audit commit scope and working tree**

```bash
git log --oneline --decorate -10
git diff HEAD^ --check
git status --short
```

Inspect each optimizer commit with `git show --stat` and `git show`. Confirm no
commit contains `docs/desktop-state/cinnamon.dconf`, `panel-backup.txt`,
`vicinae-*`, `.superpowers/`, Speed Test, or Now Playing hunks.

- [ ] **Step 6: Create a verification-fix commit only when Step 1-5 exposed a defect**

```bash
git add nothing-widgets/optimizer_core.py nothing-widgets/optimizer_guard.py nothing-widgets/test_optimizer_core.py nothing-widgets/nothing-widgets.py nothing-widgets/cleaner_core.py nothing-widgets/test_cleaner_core.py
git diff --cached --check
git commit -m "Fix optimizer verification issues"
```

Skip this commit when no source change was necessary. Before committing, inspect
the cached GTK and cleaner diffs again to exclude pre-existing hunks.
