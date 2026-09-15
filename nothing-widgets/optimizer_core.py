#!/usr/bin/env python3
"""Display-independent domain models and policies for AGENT WORK."""
import dataclasses
import json
import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, Tuple


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
    cmdline: Tuple[str, ...] = ()


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
class CaptureState:
    active: bool
    brave: bool
    certain: bool


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
    """Return the safe resource policy for a process role in a given mode."""
    mode = Mode(mode)
    role = Role(role)
    if role in (Role.MEETING, Role.PROTECTED):
        return RolePolicy(2500, 1000, 0, "protected", "realtime")
    policy = _POLICIES[mode][role]
    if mode == Mode.AUTO and role == Role.PRIMARY and not active_primary:
        return RolePolicy(200, 180, 0, "normal", "normal")
    return policy


_SETTINGS_VERSION = 1


def load_settings(path) -> OptimizerSettings:
    """Load the persisted manual-primary override, or return safe defaults."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return OptimizerSettings()
        if data.get("version") != _SETTINGS_VERSION:
            return OptimizerSettings()
        manual_primary = data.get("manual_primary", "")
        if not isinstance(manual_primary, str):
            return OptimizerSettings()
        return OptimizerSettings(manual_primary=manual_primary)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return OptimizerSettings()


def save_settings(path, settings) -> bool:
    """Atomically persist only the manual-primary override with owner-only access."""
    temporary_path = ""
    try:
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(
            prefix=".optimizer-settings-", dir=directory, text=True
        )
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                {"version": _SETTINGS_VERSION,
                 "manual_primary": settings.manual_primary},
                fh,
            )
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary_path, path)
        return True
    except (OSError, TypeError, ValueError):
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
        return False


AGENT_EXECUTABLES = frozenset(("claude", "codex"))
INTERACTIVE_EXECUTABLES = {
    "brave": "Brave",
    "brave-browser": "Brave",
    "code": "VS Code",
}
MEDIA_EXECUTABLES = {
    "spotify": "Spotify",
    "vlc": "VLC",
    "telegram-desktop": "Telegram",
    "Telegram": "Telegram",
}
PROTECTED_EXECUTABLES = frozenset((
    "systemd", "cinnamon", "cinnamon-session", "muffin", "Xorg", "Xwayland",
    "pipewire", "wireplumber", "NetworkManager", "dbus-daemon",
    "nothing-widgets.py", "optimizer_guard.py",
))
PROTECTED_PYTHON_SCRIPTS = frozenset((
    "nothing-widgets.py",
    "optimizer_guard.py",
))
PYTHON_OPTIONS_WITH_ARGUMENT = frozenset((
    "-W",
    "-X",
    "--check-hash-based",
))
PYTHON_OPTIONS_WITHOUT_SCRIPT = frozenset((
    "-c",
    "-m",
    "--help",
    "--help-all",
    "--help-env",
    "--help-xoptions",
    "--version",
))
PYTHON_OPTIONS_WITHOUT_ARGUMENT = frozenset((
    "-b",
    "-B",
    "-d",
    "-E",
    "-h",
    "-i",
    "-I",
    "-O",
    "-OO",
    "-P",
    "-q",
    "-s",
    "-S",
    "-u",
    "-v",
    "-V",
    "-x",
))


def _parse_stat(raw):
    """Return a process parent PID and start time from one procfs stat record."""
    close = raw.rfind(")")
    if close < 0:
        raise ValueError("proc stat has no closing process name")
    fields = raw[close + 2:].split()
    if len(fields) < 20:
        raise ValueError("proc stat has too few fields")
    return int(fields[1]), int(fields[19])


def _read_uid(status_path):
    with open(status_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("Uid:"):
                fields = line.split()
                if len(fields) < 2:
                    break
                return int(fields[1])
    raise ValueError("proc status has no real UID")


def _read_cgroup(cgroup_path):
    try:
        with open(cgroup_path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("0::"):
                    return line[3:].strip()
    except OSError:
        pass
    return ""


def _read_rss_bytes(statm_path):
    try:
        with open(statm_path, encoding="utf-8") as fh:
            fields = fh.read().split()
        if len(fields) >= 2:
            return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        pass
    return 0


def _read_comm(comm_path):
    try:
        with open(comm_path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _read_cmdline(cmdline_path):
    try:
        with open(cmdline_path, "rb") as fh:
            return tuple(
                os.fsdecode(argument)
                for argument in fh.read().split(b"\0")
                if argument
            )
    except OSError:
        return ()


def read_processes(proc_root="/proc", uid=os.getuid()) -> Tuple[ProcessInfo, ...]:
    """Read only verifiably current-user processes from a procfs tree."""
    processes = []
    try:
        with os.scandir(proc_root) as entries:
            pids = sorted(
                int(entry.name) for entry in entries
                if entry.name.isascii() and entry.name.isdecimal()
            )
    except OSError:
        return ()

    for pid in pids:
        process_dir = os.path.join(proc_root, str(pid))
        try:
            with open(os.path.join(process_dir, "stat"), encoding="utf-8") as fh:
                ppid, start_time = _parse_stat(fh.read())
            process_uid = _read_uid(os.path.join(process_dir, "status"))
        except (OSError, ValueError):
            continue
        if process_uid != uid:
            continue
        try:
            exe = os.readlink(os.path.join(process_dir, "exe"))
        except OSError:
            exe = ""
        processes.append(ProcessInfo(
            identity=ProcessIdentity(pid, start_time),
            ppid=ppid,
            uid=process_uid,
            comm=_read_comm(os.path.join(process_dir, "comm")),
            exe=exe,
            cgroup=_read_cgroup(os.path.join(process_dir, "cgroup")),
            rss_bytes=_read_rss_bytes(os.path.join(process_dir, "statm")),
            cmdline=_read_cmdline(os.path.join(process_dir, "cmdline")),
        ))
    return tuple(processes)


def descendants(processes, roots):
    """Return every PID in the process forest rooted at ``roots``."""
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


def same_process(identity, proc_root="/proc", source=None) -> bool:
    """Check that a PID still names the exact process start instance."""
    if identity.pid <= 0 or identity.start_time <= 0:
        return False
    try:
        if source is not None:
            start_time = source.start_time(identity.pid)
        else:
            stat_path = os.path.join(proc_root, str(identity.pid), "stat")
            with open(stat_path, encoding="utf-8") as fh:
                _ppid, start_time = _parse_stat(fh.read())
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return start_time == identity.start_time


def _executable_name(process):
    return os.path.basename(process.exe)


def _python_script_path(process):
    executable = _executable_name(process)
    if not executable.startswith(("python", "pypy")):
        return "", False
    index = 1
    while index < len(process.cmdline):
        argument = process.cmdline[index]
        if argument == "--":
            index += 1
            return (
                (process.cmdline[index], False)
                if index < len(process.cmdline) else ("", True)
            )
        if argument in PYTHON_OPTIONS_WITHOUT_SCRIPT:
            return "", False
        if argument in PYTHON_OPTIONS_WITH_ARGUMENT:
            if index + 1 >= len(process.cmdline):
                return "", True
            index += 2
            continue
        if argument.startswith(("-W", "-X")) and len(argument) > 2:
            index += 1
            continue
        if argument.startswith("--check-hash-based="):
            index += 1
            continue
        if argument in PYTHON_OPTIONS_WITHOUT_ARGUMENT:
            index += 1
            continue
        if argument.startswith("-"):
            return "", True
        return argument, False
    return "", False


def _is_protected_python_script(process):
    script_path, _ambiguous = _python_script_path(process)
    return (
        os.path.isabs(script_path)
        and os.path.basename(script_path) in PROTECTED_PYTHON_SCRIPTS
    )


def _is_ambiguous_python_invocation(process):
    _script_path, ambiguous = _python_script_path(process)
    return ambiguous


def _is_protected(process):
    return (
        _executable_name(process) in PROTECTED_EXECUTABLES
        or _is_protected_python_script(process)
    )


def _has_stable_identity(process):
    return process.identity.pid > 0 and process.identity.start_time > 0


def _has_stable_cgroup(process):
    return bool(process.cgroup and process.cgroup.strip() not in ("", "/"))


def _agent_uncertainty_reason(processes):
    reasons = []
    if any(not _has_stable_identity(process) for process in processes):
        reasons.append("unresolved identity")
    if any(not _has_stable_cgroup(process) for process in processes):
        reasons.append("unresolved cgroup")
    if any(_is_ambiguous_python_invocation(process) for process in processes):
        reasons.append("ambiguous Python invocation")
    return "agent tree has " + " and ".join(reasons)


def _unit_for(cgroup):
    return cgroup.rstrip("/").rsplit("/", 1)[-1] if cgroup else ""


def _key_for_cgroup(cgroup):
    key = _unit_for(cgroup)
    if key.endswith(".scope"):
        key = key[:-6]
    if key.startswith("app-"):
        key = key[4:]
    return key or "application"


def _label_and_role(processes):
    if any(_is_protected(process) for process in processes):
        return "Protected service", Role.PROTECTED
    for process in processes:
        name = _executable_name(process)
        if name in INTERACTIVE_EXECUTABLES:
            return INTERACTIVE_EXECUTABLES[name], Role.INTERACTIVE
    for process in processes:
        name = _executable_name(process)
        if name in MEDIA_EXECUTABLES:
            return MEDIA_EXECUTABLES[name], Role.MEDIA
    return _unit_for(processes[0].cgroup) or processes[0].comm or "Process", Role.BACKGROUND


def _unmanaged_group(process, reason, role=None):
    label, default_role = _label_and_role((process,))
    if role is None:
        role = default_role
    return AppGroup(
        key="process-%d-%d" % (process.identity.pid, process.identity.start_time),
        label=label,
        role=role,
        processes=(process,),
        managed=False,
        reason=reason,
    )


def _agent_groups(processes):
    by_pid = {process.identity.pid: process for process in processes}
    candidates = {
        process.identity.pid for process in processes
        if _executable_name(process) in AGENT_EXECUTABLES
    }
    members = {candidate: [] for candidate in candidates}
    for process in processes:
        parent = process.identity.pid
        ancestors = set()
        while parent in by_pid and parent not in ancestors:
            if parent in candidates:
                members[parent].append(process)
                break
            ancestors.add(parent)
            parent = by_pid[parent].ppid
    return tuple(
        (root_pid, tuple(members[root_pid]))
        for root_pid in sorted(candidates)
    )


def classify_groups(processes, desktop_apps=(), capture=None, manual_primary="",
                    uid=os.getuid()) -> Tuple[AppGroup, ...]:
    """Classify current-user process trees into safe, exact application groups."""
    del desktop_apps, capture
    current = tuple(
        process for process in processes
        if process.uid == uid
    )
    stable = tuple(process for process in current if _has_stable_identity(process))
    groups = []
    assigned = set()

    for root_pid, members in _agent_groups(current):
        assigned.update(process.identity for process in members)
        root = next(process for process in members if process.identity.pid == root_pid)
        if not all(
            _has_stable_identity(process)
            and _has_stable_cgroup(process)
            and not _is_ambiguous_python_invocation(process)
            for process in members
        ):
            reason = _agent_uncertainty_reason(members)
            for process in members:
                role = Role.PROTECTED if _is_protected(process) else Role.PRIMARY
                groups.append(_unmanaged_group(
                    process,
                    reason,
                    role=role,
                ))
            continue
        role = Role.PROTECTED if any(_is_protected(process) for process in members) else Role.PRIMARY
        agent_name = _executable_name(root).title()
        groups.append(AppGroup(
            key="agent-%d-%d" % (root.identity.pid, root.identity.start_time),
            label="%s session" % agent_name,
            role=role,
            processes=members,
            unit=_unit_for(root.cgroup) if _has_stable_cgroup(root) else "",
            reason="agent process tree",
        ))

    for process in current:
        if not _has_stable_identity(process) and process.identity not in assigned:
            groups.append(_unmanaged_group(process, "unresolved process identity"))
            assigned.add(process.identity)

    pending = tuple(process for process in stable if process.identity not in assigned)
    by_cgroup = {}
    for process in pending:
        if _has_stable_cgroup(process) and not _is_ambiguous_python_invocation(process):
            by_cgroup.setdefault(process.cgroup, []).append(process)
        else:
            reason = (
                "ambiguous Python invocation"
                if _is_ambiguous_python_invocation(process)
                else "missing stable cgroup"
            )
            groups.append(_unmanaged_group(process, reason))

    used_keys = {group.key for group in groups}
    for cgroup, members in sorted(by_cgroup.items(), key=lambda item: item[1][0].identity.pid):
        members = tuple(members)
        label, role = _label_and_role(members)
        key = _key_for_cgroup(cgroup)
        if key in used_keys:
            key = "%s-%d" % (key, members[0].identity.pid)
        used_keys.add(key)
        if manual_primary and manual_primary == key and role != Role.PROTECTED:
            role = Role.PRIMARY
        groups.append(AppGroup(
            key=key,
            label=label,
            role=role,
            processes=members,
            unit=_unit_for(cgroup),
            reason="stable cgroup",
        ))
    return tuple(groups)


def _run_probe(runner, command):
    """Run an injected read-only probe and normalize a failed result."""
    try:
        result = runner(command) if callable(runner) else runner.run(command)
        if not isinstance(result, tuple) or len(result) < 2:
            return False, ""
        return bool(result[0]), str(result[1] or "")
    except Exception:
        return False, ""


def _is_brave_identity(properties):
    """Match only application identity fields, never stream or device metadata."""
    for name in ("application.process.binary", "application.name"):
        value = properties.get(name, "")
        if isinstance(value, str) and "brave" in value.casefold():
            return True
    return False


def _has_application_identity(properties):
    return any(
        isinstance(properties.get(name), str) and bool(properties[name].strip())
        for name in ("application.process.binary", "application.name")
    )


def _pactl_capture_state(output):
    records = json.loads(output)
    if isinstance(records, dict):
        records = records.get("source-outputs", list(records.values()))
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise ValueError("invalid source-output metadata")
    properties = []
    for record in records:
        value = record.get("properties", {})
        if not isinstance(value, dict):
            raise ValueError("invalid source-output properties")
        properties.append(value)
    return CaptureState(
        active=bool(properties),
        brave=any(_is_brave_identity(value) for value in properties),
        certain=all(_has_application_identity(value) for value in properties),
    )


def _pipewire_capture_state(output):
    nodes = json.loads(output)
    if not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes):
        raise ValueError("invalid PipeWire metadata")
    captures = []
    for node in nodes:
        info = node.get("info", {})
        if not isinstance(info, dict):
            continue
        properties = info.get("props", {})
        if not isinstance(properties, dict):
            continue
        media_class = properties.get("media.class", "")
        if isinstance(media_class, str) and media_class.startswith("Stream/Input/"):
            captures.append(properties)
    return CaptureState(
        active=bool(captures),
        brave=any(_is_brave_identity(properties) for properties in captures),
        certain=all(_has_application_identity(properties) for properties in captures),
    )


def capture_state(runner) -> CaptureState:
    """Read capture activity from server metadata without inspecting media content."""
    ok, output = _run_probe(runner, ("pactl", "-f", "json", "list", "source-outputs"))
    if ok:
        try:
            return _pactl_capture_state(output)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    ok, output = _run_probe(runner, ("pw-dump",))
    if ok:
        try:
            return _pipewire_capture_state(output)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return CaptureState(active=False, brave=False, certain=False)


def promote_capture(groups, state) -> Tuple[AppGroup, ...]:
    """Promote Brave while capture is attributed or uncertain and active."""
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


def detect_capabilities(runner) -> Tuple[Capability, ...]:
    """Report only capabilities that probes can establish without blocking callers."""
    helper_ok, helper_output = _run_probe(
        runner, ("nothing-optimizer-helper", "--capabilities")
    )
    power_ok, _power_output = _run_probe(runner, ("powerprofilesctl", "get"))
    try:
        helper_capabilities = json.loads(helper_output)
        network_rules_active = (
            isinstance(helper_capabilities, dict)
            and helper_capabilities.get("network_rules_active") is True
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        network_rules_active = False
    network = (
        Capability("network", "full", "Privileged network helper is available")
        if helper_ok and network_rules_active else
        Capability("network", "fallback", "BASIC network policy only; active helper rules unverified")
    )
    power = (
        Capability("power", "full", "power-profiles service is available")
        if power_ok else
        Capability("power", "unavailable", "power-profiles service is unavailable")
    )
    return network, power


POWER_PROFILE = {
    Mode.AUTO: "balanced",
    Mode.PERFORMANCE: "performance",
    Mode.BATTERY: "power-saver",
    Mode.FOCUS: "balanced",
}


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


class NetworkBackend(Protocol):
    def apply(self, groups, policies) -> Capability: ...

    def restore(self) -> Capability: ...


class BasicNetworkBackend:
    """A truthful placeholder until the separate network helper is installed."""

    def apply(self, groups, policies) -> Capability:
        return Capability("network", "fallback", "NETWORK · BASIC")

    def restore(self) -> Capability:
        return Capability("network", "fallback", "NETWORK · BASIC")


class ResourceController:
    """Apply best-effort policy and retain only successful, reversible writes.

    ``runner`` is an argument-list command runner. ``source`` optionally supplies
    start_time(pid), as in same_process; production defaults to fresh procfs reads.
    Failed restores remain pending for retry. External changes relinquish ownership.
    """

    def __init__(self, runner, uid=os.getuid(), network=None, source=None):
        self.runner = runner
        self.uid = uid
        self.network = network if network is not None else BasicNetworkBackend()
        self.source = source
        self._profile_read = False
        self._profile_original = None
        self._profile_owned = None
        self._units = {}
        self._unit_pending = {}
        self._unit_identities = {}
        self._processes = {}

    def _run(self, command):
        return _run_probe(self.runner, tuple(command))

    def _current(self, identity):
        return same_process(identity, source=self.source)

    def _power(self, mode):
        if not self._profile_read:
            self._profile_read = True
            ok, value = self._run(("powerprofilesctl", "get"))
            if ok and value.strip() in set(POWER_PROFILE.values()):
                self._profile_original = value.strip()
        wanted = POWER_PROFILE[mode]
        if self._profile_original is not None:
            ok, _ = self._run(("powerprofilesctl", "set", wanted))
            if ok:
                self._profile_owned = wanted
                return Capability("power", "full", "Platform profile: " + wanted)
        return Capability("power", "unavailable", "Platform profile change unavailable")

    def _unit_values(self, unit):
        ok, output = self._run(("systemctl", "--user", "show", unit,
                                "--property=CPUWeight,IOWeight,MemoryLow"))
        values = {}
        if ok:
            for line in output.splitlines():
                name, sep, value = line.partition("=")
                if sep and name in ("CPUWeight", "IOWeight", "MemoryLow"):
                    if value.isascii() and value.isdecimal():
                        values[name] = str(int(value))
        return values

    def _unit_apply(self, group, policy):
        identities = tuple(p.identity for p in group.processes)
        previous = self._unit_identities.get(group.unit, ())
        if previous and (not set(previous).intersection(identities)
                         or not all(self._current(i) for i in previous)):
            self._forget_unit(group.unit)
        before = self._unit_values(group.unit)
        self._reconcile_unit(group.unit, before)
        if len(before) != 3 or not all(self._current(i) for i in identities):
            return set()
        desired = {"CPUWeight": str(policy.cpu_weight), "IOWeight": str(policy.io_weight),
                   "MemoryLow": "268435456" if policy.memory_protection == "protected" else "0"}
        command = ("systemctl", "--user", "set-property", "--runtime", group.unit,
                   "CPUWeight=%d" % policy.cpu_weight, "IOWeight=%d" % policy.io_weight,
                   "MemoryLow=%s" % ("256M" if policy.memory_protection == "protected" else "0"))
        ok, _ = self._run(command)
        journal = self._units.setdefault(group.unit, {})
        pending = self._unit_pending.setdefault(group.unit, {})
        for name, value in desired.items():
            original = journal.get(name, (before[name], None))[0]
            if ok and (before[name] != value or name in journal):
                journal[name] = (original, value)
            elif not ok and before[name] != value:
                # This is an attempted write, not yet a confirmed owned change.
                # Keep it separately until a later read establishes its outcome.
                pending[name] = (original, value)
        # A multi-property call can fail after applying a subset. Read back that
        # subset so one unsupported property cannot lose successful mutations.
        after = desired if ok else self._unit_values(group.unit)
        succeeded = {name for name, value in desired.items()
                     if after.get(name) == value and (ok or before[name] != value)}
        self._reconcile_unit(group.unit, after)
        if self._units.get(group.unit) or self._unit_pending.get(group.unit):
            self._unit_identities[group.unit] = identities
        else:
            self._forget_unit(group.unit)
        return succeeded

    def _forget_unit(self, unit):
        self._units.pop(unit, None)
        self._unit_pending.pop(unit, None)
        self._unit_identities.pop(unit, None)

    def _reconcile_unit(self, unit, current):
        """Resolve attempted writes and relinquish externally changed values."""
        journal = self._units.get(unit, {})
        pending = self._unit_pending.get(unit, {})
        for name, (original, requested) in tuple(pending.items()):
            if name not in current:
                continue
            if current[name] == requested:
                journal[name] = (original, requested)
            del pending[name]
        for name, (_original, owned) in tuple(journal.items()):
            if name in current and current[name] != owned:
                del journal[name]
        if journal:
            self._units[unit] = journal
        else:
            self._units.pop(unit, None)
        if not pending:
            self._unit_pending.pop(unit, None)

    def _process_value(self, identity, kind):
        pid = str(identity.pid)
        if kind == "cpu":
            ok, output = self._run(("ps", "-o", "ni=", "-p", pid))
            try:
                value = int(output.strip())
                return value if ok and 0 <= value <= 19 else None
            except ValueError:
                return None
        ok, output = self._run(("ionice", "-p", pid))
        if not ok:
            return None
        output = output.strip()
        if output in ("none: prio 0", "none"):
            return (0, 0)
        if output in ("idle", "idle: prio 0"):
            return (3, 0)
        prefix = "best-effort: prio "
        if output.startswith(prefix) and output[len(prefix):] in tuple(str(i) for i in range(8)):
            return (2, int(output[len(prefix):]))
        return None

    def _process_command(self, identity, kind, value):
        if kind == "cpu":
            return ("renice", "-n", str(value), "-p", str(identity.pid))
        io_class, priority = value
        command = ("ionice", "-c", str(io_class))
        if io_class == 2:
            command += ("-n", str(priority))
        return command + ("-p", str(identity.pid))

    def _process_apply(self, group, policy):
        applied = []
        for process in group.processes:
            identity = process.identity
            changed = False
            for kind, wanted in (("cpu", max(0, min(19, policy.nice))),
                                 ("io", (2, 7 if policy.nice >= 5 else 4))):
                if not self._current(identity):
                    continue
                before = self._process_value(identity, kind)
                key = (identity, kind)
                if (before is not None and key in self._processes
                        and before != self._processes[key][1]):
                    del self._processes[key]
                if before is None or not self._current(identity):
                    continue
                ok, _ = self._run(self._process_command(identity, kind, wanted))
                if ok:
                    original = self._processes.get(key, (before, None))[0]
                    if before != wanted or key in self._processes:
                        self._processes[key] = (original, wanted)
                    changed = True
            if changed:
                applied.append(identity)
        return tuple(applied)

    def apply(self, mode, groups, active_primary=True) -> AppliedState:
        mode = Mode(mode)
        groups = tuple(groups)
        power = self._power(mode)
        applied = []
        unit_count = {}
        for group in groups:
            unit_count[group.unit] = unit_count.get(group.unit, 0) + 1
        policies = {}
        eligible = []
        full_cpu_io = full_memory = 0
        any_cpu_io = False
        for group in groups:
            if (not group.managed or not group.processes
                    or any(p.uid != self.uid or not self._current(p.identity)
                           for p in group.processes)):
                continue
            eligible.append(group)
            policy = policy_for(mode, group.role, active_primary)
            policies[group.key] = policy
            properties = set()
            # Shared units cannot represent independently classified app groups.
            if (group.unit and not group.unit.startswith("-") and "/" not in group.unit
                    and group.unit.endswith((".scope", ".service")) and unit_count[group.unit] == 1):
                properties = self._unit_apply(group, policy)
            full_memory += "MemoryLow" in properties
            if {"CPUWeight", "IOWeight"} <= properties:
                full_cpu_io += 1
                identities = tuple(p.identity for p in group.processes)
                backend = "systemd"
            else:
                identities = self._process_apply(group, policy)
                backend = "process"
            any_cpu_io |= bool(identities or properties & {"CPUWeight", "IOWeight"})
            if properties or identities:
                if properties and not identities:
                    identities = tuple(p.identity for p in group.processes)
                    backend = "systemd"
                applied.append(AppliedGroupState(group.key, group.unit, identities, backend))
        cpu_level = "full" if eligible and full_cpu_io == len(eligible) else (
            "fallback" if any_cpu_io else "unavailable")
        memory_level = "full" if eligible and full_memory == len(eligible) else (
            "fallback" if full_memory else "unavailable")
        try:
            network = self.network.apply(tuple(eligible), policies)
        except Exception:
            network = Capability("network", "unavailable", "Network backend failed")
        return AppliedState(mode, tuple(applied), (
            power, Capability("cpu_io", cpu_level, "User weights or safe process priorities"),
            Capability("memory", memory_level, "Soft memory protection only"), network))

    def restore(self) -> Tuple[Capability, ...]:
        failures = set()
        for (identity, kind), (original, owned) in tuple(self._processes.items()):
            key = (identity, kind)
            if not self._current(identity):
                del self._processes[key]
                continue
            current = self._process_value(identity, kind)
            if current is None:
                failures.add("cpu_io")
                continue
            if current != owned:
                del self._processes[key]
                continue
            if not self._current(identity):
                del self._processes[key]
                continue
            ok, _ = self._run(self._process_command(identity, kind, original))
            if ok:
                del self._processes[key]
            else:
                failures.add("cpu_io")
        for unit, identities in tuple(self._unit_identities.items()):
            if not all(self._current(i) for i in identities):
                self._forget_unit(unit)
                continue
            current = self._unit_values(unit)
            self._reconcile_unit(unit, current)
            for name in self._unit_pending.get(unit, {}):
                failures.add("memory" if name == "MemoryLow" else "cpu_io")
            journal = self._units.get(unit, {})
            for name, (original, owned) in tuple(journal.items()):
                capability = "memory" if name == "MemoryLow" else "cpu_io"
                if name not in current:
                    failures.add(capability)
                    continue
                if current[name] != owned:
                    del journal[name]
                    continue
                if not all(self._current(i) for i in identities):
                    del journal[name]
                    continue
                # systemd represents an unset weight as UINT64_MAX; reset it
                # with the documented empty assignment, not an invalid weight.
                value = "" if name != "MemoryLow" and original == str(2**64 - 1) else original
                ok, _ = self._run(("systemctl", "--user", "set-property", "--runtime",
                                   unit, "%s=%s" % (name, value)))
                if ok:
                    del journal[name]
                else:
                    failures.add(capability)
            if not journal and not self._unit_pending.get(unit):
                self._forget_unit(unit)
        try:
            network = self.network.restore()
        except Exception:
            network = Capability("network", "unavailable", "Network restore failed")
        if self._profile_owned is not None:
            ok, current = self._run(("powerprofilesctl", "get"))
            if not ok or current.strip() not in set(POWER_PROFILE.values()):
                failures.add("power")
            elif current.strip() != self._profile_owned:
                self._profile_owned = None
            else:
                ok, _ = self._run(("powerprofilesctl", "set", self._profile_original))
                if ok:
                    self._profile_owned = None
                else:
                    failures.add("power")
        if self._profile_owned is None:
            self._profile_read = False
            self._profile_original = None
        return tuple(Capability(name, "unavailable" if name in failures else "full",
                                "Restore pending" if name in failures else "Restore complete")
                     for name in ("cpu_io", "memory", "power")) + (network,)
