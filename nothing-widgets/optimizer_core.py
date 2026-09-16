#!/usr/bin/env python3
"""Display-independent domain models and policies for AGENT WORK."""
import dataclasses
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
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
    end_token: str = ""


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

    def __init__(self, runner, uid=os.getuid(), network=None, source=None,
                 journal=None, journal_path=None):
        self.runner = runner
        self.uid = uid
        self.network = network if network is not None else BasicNetworkBackend()
        self.source = source
        self.journal = journal or RuntimeJournal(journal_path)
        self._profile_read = False
        self._profile_original = None
        self._profile_owned = None
        self._units = {}
        self._unit_pending = {}
        self._unit_identities = {}
        self._unit_group_keys = {}
        self._processes = {}
        self._process_group_keys = {}

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
            self._unit_group_keys[group.unit] = group.key
        else:
            self._forget_unit(group.unit)
        return succeeded

    def _forget_unit(self, unit):
        self._units.pop(unit, None)
        self._unit_pending.pop(unit, None)
        self._unit_identities.pop(unit, None)
        self._unit_group_keys.pop(unit, None)

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
                        self._process_group_keys[key] = group.key
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
        self._sync_runtime_journal()
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
        self._sync_runtime_journal()
        return tuple(Capability(name, "unavailable" if name in failures else "full",
                                "Restore pending" if name in failures else "Restore complete")
                     for name in ("cpu_io", "memory", "power")) + (network,)

    def _sync_runtime_journal(self):
        """Persist only confirmed resource ownership needed after a crash."""
        entries = []
        for unit, properties in self._units.items():
            identities = self._unit_identities.get(unit, ())
            if not properties or not identities:
                continue
            entries.append({
                "action": "resource",
                "group_key": self._unit_group_keys.get(unit, unit),
                "identities": [_identity_data(identity) for identity in identities],
                "unit": unit,
                "original": {name: original for name, (original, _owned) in properties.items()},
                "owned": {name: owned for name, (_original, owned) in properties.items()},
            })
        process_entries = {}
        for (identity, kind), (original, owned) in self._processes.items():
            key = (identity, self._process_group_keys.get((identity, kind), "process"))
            process_entries.setdefault(key, [{}, {}])[0][kind] = original
            process_entries[key][1][kind] = owned
        for (identity, group_key), (original, owned) in process_entries.items():
            entries.append({
                "action": "resource",
                "group_key": group_key,
                "identities": [_identity_data(identity)],
                "unit": "",
                "original": {
                    name: list(value) if isinstance(value, tuple) else value
                    for name, value in original.items()
                },
                "owned": {
                    name: list(value) if isinstance(value, tuple) else value
                    for name, value in owned.items()
                },
            })
        self.journal.replace_action("resource", entries)


RUNTIME_JOURNAL_VERSION = 1


def runtime_journal_path():
    """Return the owner-only runtime-state path without creating it."""
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if not config_home:
        config_home = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(config_home, "nothing-widgets", "optimizer-runtime.json")


def _empty_runtime_journal():
    return {"version": RUNTIME_JOURNAL_VERSION, "entries": []}


def _valid_identity_data(value):
    return (
        isinstance(value, dict)
        and set(value) == {"pid", "start_time"}
        and isinstance(value["pid"], int)
        and not isinstance(value["pid"], bool)
        and value["pid"] > 0
        and isinstance(value["start_time"], int)
        and not isinstance(value["start_time"], bool)
        and value["start_time"] > 0
    )


def _valid_runtime_entry(value):
    if not isinstance(value, dict):
        return False
    common = {"action", "group_key", "identities", "unit", "original", "owned"}
    if value.get("action") == "pause":
        if set(value) != common | {"backend"}:
            return False
    elif set(value) != common:
        return False
    if value["action"] not in ("pause", "resource"):
        return False
    if not isinstance(value["group_key"], str) or not isinstance(value["unit"], str):
        return False
    if not isinstance(value["identities"], list) or not value["identities"]:
        return False
    if not all(_valid_identity_data(identity) for identity in value["identities"]):
        return False
    if not isinstance(value["original"], dict) or not isinstance(value["owned"], dict):
        return False
    if value["action"] == "pause":
        if value["backend"] not in ("process", "systemd", "systemd-uncertain"):
            return False
        if value["backend"] in ("systemd", "systemd-uncertain"):
            return (
                bool(value["unit"])
                and not value["unit"].startswith("-")
                and "/" not in value["unit"]
                and value["unit"].endswith((".scope", ".service"))
                and value["original"] == {"state": "running"}
                and value["owned"] == {"state": "frozen"}
            )
        return (
            value["original"] == {"state": "running"}
            and value["owned"] == {"state": "stopped"}
        )
    if set(value["original"]) != set(value["owned"]) or not value["owned"]:
        return False
    if value["unit"]:
        return (
            not value["unit"].startswith("-")
            and "/" not in value["unit"]
            and value["unit"].endswith((".scope", ".service"))
            and set(value["owned"]) <= {"CPUWeight", "IOWeight", "MemoryLow"}
            and all(isinstance(state, str) and state.isdecimal()
                    for state in value["original"].values())
            and all(isinstance(state, str) and state.isdecimal()
                    for state in value["owned"].values())
        )
    if len(value["identities"]) != 1 or not set(value["owned"]) <= {"cpu", "io"}:
        return False
    for name, state in value["original"].items():
        owned = value["owned"][name]
        if name == "cpu":
            if (not isinstance(state, int) or isinstance(state, bool) or not 0 <= state <= 19
                    or not isinstance(owned, int) or isinstance(owned, bool) or not 0 <= owned <= 19):
                return False
        elif name == "io":
            if not all(
                isinstance(item, list) and len(item) == 2
                and all(isinstance(part, int) and not isinstance(part, bool) for part in item)
                and item[0] in (0, 2, 3) and 0 <= item[1] <= 7
                for item in (state, owned)
            ):
                return False
    return True


def _valid_runtime_journal(value):
    return (
        isinstance(value, dict)
        and set(value) == {"version", "entries"}
        and isinstance(value["version"], int)
        and not isinstance(value["version"], bool)
        and value["version"] == RUNTIME_JOURNAL_VERSION
        and isinstance(value["entries"], list)
        and all(_valid_runtime_entry(entry) for entry in value["entries"])
    )


def load_runtime_journal(path=None):
    """Load validated crash state; malformed state is never acted upon."""
    path = path or runtime_journal_path()
    try:
        with open(path, encoding="utf-8") as fh:
            journal = json.load(fh)
    except FileNotFoundError:
        return _empty_runtime_journal(), Capability(
            "journal", "full", "Runtime journal is empty")
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _empty_runtime_journal(), Capability(
            "journal", "unavailable", "Runtime journal could not be read")
    if not _valid_runtime_journal(journal):
        return _empty_runtime_journal(), Capability(
            "journal", "unavailable", "Runtime journal was corrupt and was ignored")
    return journal, Capability("journal", "full", "Runtime journal loaded")


def save_runtime_journal(path, journal):
    """Atomically replace runtime state using an owner-only temporary file."""
    if not _valid_runtime_journal(journal):
        return False
    temporary_path = path + ".tmp"
    fd = None
    try:
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = None
            json.dump(journal, fh, separators=(",", ":"), sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary_path, path)
        return True
    except (OSError, TypeError, ValueError):
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        return False


class MemoryJournal:
    """A small injectable journal for tests and display-only integrations."""

    def __init__(self, journal=None):
        self.journal = journal if _valid_runtime_journal(journal) else _empty_runtime_journal()

    def load(self):
        return json.loads(json.dumps(self.journal)), Capability(
            "journal", "full", "Runtime journal loaded")

    def replace_action(self, action, entries):
        current, _capability = self.load()
        current["entries"] = [
            entry for entry in current["entries"] if entry["action"] != action
        ] + list(entries)
        if not _valid_runtime_journal(current):
            return False
        self.journal = current
        return True

    def replace_entries(self, entries):
        current = {"version": RUNTIME_JOURNAL_VERSION, "entries": list(entries)}
        if not _valid_runtime_journal(current):
            return False
        self.journal = current
        return True


class RuntimeJournal:
    """Persistent journal adapter shared by action and resource controllers."""

    def __init__(self, path=None):
        self.path = path or runtime_journal_path()

    def load(self):
        return load_runtime_journal(self.path)

    def replace_action(self, action, entries):
        current, capability = self.load()
        if capability.level == "unavailable":
            current = _empty_runtime_journal()
        current["entries"] = [
            entry for entry in current["entries"] if entry["action"] != action
        ] + list(entries)
        return save_runtime_journal(self.path, current)

    def replace_entries(self, entries):
        return save_runtime_journal(
            self.path,
            {"version": RUNTIME_JOURNAL_VERSION, "entries": list(entries)},
        )


def _identity_data(identity):
    return {"pid": identity.pid, "start_time": identity.start_time}


def _identity_from_data(value):
    return ProcessIdentity(value["pid"], value["start_time"])


def _current_user_identity(identity, source=None, uid=None):
    """Check an exact live identity and, for procfs, its current-user ownership."""
    if not same_process(identity, source=source):
        return False
    if source is not None:
        # Test/injected sources provide no UID surface; production uses procfs below.
        return True
    try:
        return os.stat(os.path.join("/proc", str(identity.pid))).st_uid == (
            os.getuid() if uid is None else uid
        )
    except OSError:
        return False


def _process_state(identity, source=None):
    """Return the current procfs state for one exact identity, or ``""``."""
    if not same_process(identity, source=source):
        return ""
    try:
        if source is not None and hasattr(source, "state"):
            value = source.state(identity.pid)
            return value if isinstance(value, str) and len(value) == 1 else ""
        with open(os.path.join("/proc", str(identity.pid), "stat"), encoding="utf-8") as fh:
            raw = fh.read()
        fields = raw[raw.rfind(")") + 2:].split()
        return fields[0] if fields else ""
    except (AttributeError, OSError, TypeError, ValueError):
        return ""


def _is_stopped_state(state):
    return state in ("T", "t")


def _exclusive_managed_unit(group_key, unit, identities, groups_provider,
                            source=None, uid=None):
    """Prove that a current discovery snapshot still owns one whole user unit."""
    if groups_provider is None:
        return False
    try:
        groups = tuple(groups_provider())
    except Exception:
        return False
    members = tuple(group for group in groups if group.unit == unit)
    if len(members) != 1:
        return False
    group = members[0]
    expected_uid = os.getuid() if uid is None else uid
    return (
        group.key == group_key
        and group.managed
        and tuple(process.identity for process in group.processes) == tuple(identities)
        and all(process.uid == expected_uid
                and _current_user_identity(process.identity, source=source, uid=expected_uid)
                for process in group.processes)
    )


def _default_runner(command):
    try:
        completed = subprocess.run(
            tuple(command),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
        return completed.returncode == 0, completed.stdout
    except (OSError, subprocess.SubprocessError):
        return False, ""


def _process_restore_value(runner, identity, kind):
    pid = str(identity.pid)
    if kind == "cpu":
        ok, output = _run_probe(runner, ("ps", "-o", "ni=", "-p", pid))
        try:
            value = int(output.strip())
            return value if ok and 0 <= value <= 19 else None
        except ValueError:
            return None
    ok, output = _run_probe(runner, ("ionice", "-p", pid))
    if not ok:
        return None
    output = output.strip()
    if output in ("none: prio 0", "none"):
        return (0, 0)
    if output in ("idle", "idle: prio 0"):
        return (3, 0)
    prefix = "best-effort: prio "
    if output.startswith(prefix) and output[len(prefix):] in tuple(str(i) for i in range(8)):
        return 2, int(output[len(prefix):])
    return None


def _process_restore_command(identity, kind, value):
    if kind == "cpu":
        return "renice", "-n", str(value), "-p", str(identity.pid)
    io_class, priority = value
    command = ("ionice", "-c", str(io_class))
    if io_class == 2:
        command += ("-n", str(priority))
    return command + ("-p", str(identity.pid),)


def _restore_resource_entry(entry, runner, source):
    """Restore one validated entry, preserving only retryable unresolved values."""
    identities = tuple(_identity_from_data(value) for value in entry["identities"])
    if not all(_current_user_identity(identity, source=source) for identity in identities):
        return None, True
    original = dict(entry["original"])
    owned = dict(entry["owned"])
    changed = False
    if entry["unit"]:
        ok, output = _run_probe(runner, (
            "systemctl", "--user", "show", entry["unit"],
            "--property=CPUWeight,IOWeight,MemoryLow",
        ))
        current = {}
        if ok:
            for line in output.splitlines():
                name, separator, value = line.partition("=")
                if separator and name in ("CPUWeight", "IOWeight", "MemoryLow"):
                    current[name] = value
        for name, expected in tuple(owned.items()):
            if name not in original:
                owned.pop(name, None)
                changed = True
                continue
            if name not in current:
                continue
            if current[name] != expected:
                original.pop(name, None)
                owned.pop(name, None)
                changed = True
                continue
            if not all(_current_user_identity(identity, source=source) for identity in identities):
                return None, True
            value = "" if name != "MemoryLow" and original[name] == str(2**64 - 1) else original[name]
            restored, _output = _run_probe(runner, (
                "systemctl", "--user", "set-property", "--runtime", entry["unit"],
                "%s=%s" % (name, value),
            ))
            if restored:
                original.pop(name, None)
                owned.pop(name, None)
                changed = True
    else:
        if len(identities) != 1:
            return None, True
        identity = identities[0]
        for kind, expected in tuple(owned.items()):
            if kind not in original or kind not in ("cpu", "io"):
                owned.pop(kind, None)
                original.pop(kind, None)
                changed = True
                continue
            expected_value = tuple(expected) if kind == "io" and isinstance(expected, list) else expected
            original_value = tuple(original[kind]) if kind == "io" and isinstance(original[kind], list) else original[kind]
            current = _process_restore_value(runner, identity, kind)
            if current is None:
                continue
            if current != expected_value:
                original.pop(kind, None)
                owned.pop(kind, None)
                changed = True
                continue
            if not _current_user_identity(identity, source=source):
                return None, True
            restored, _output = _run_probe(
                runner, _process_restore_command(identity, kind, original_value))
            if restored:
                original.pop(kind, None)
                owned.pop(kind, None)
                changed = True
    if not owned:
        return None, True
    restored = dict(entry)
    restored["original"] = original
    restored["owned"] = owned
    return restored, changed


def restore_from_journal(path=None, runner=None, source=None, journal=None,
                         send_signal=None, groups_provider=None, uid=None):
    """Idempotently restore only still-owned resource and pause transitions."""
    path = path or runtime_journal_path()
    if journal is None:
        data, capability = load_runtime_journal(path)
        replace_entries = lambda entries: save_runtime_journal(
            path, {"version": RUNTIME_JOURNAL_VERSION, "entries": entries})
    else:
        data, capability = journal.load()
        replace_entries = journal.replace_entries
    if capability.level == "unavailable":
        return (capability,)
    runner = runner or _default_runner
    send_signal = send_signal or (lambda identity, signum: os.kill(identity.pid, signum))
    retained = []
    changed = False
    for entry in data["entries"]:
        if entry["action"] == "pause":
            identities = tuple(_identity_from_data(value) for value in entry["identities"])
            if entry["backend"] == "systemd-uncertain":
                # The freezer request did not acknowledge completion.  Its
                # journal intent is deliberately durable, but is not proof
                # that this controller caused a frozen unit, so never thaw it.
                retained.append(entry)
                continue
            if entry["backend"] == "systemd":
                current = tuple(
                    identity for identity in identities
                    if _current_user_identity(identity, source=source, uid=uid)
                )
                if not current:
                    # The unit is no longer inhabited by an identity we paused.
                    # It cannot be safely attributed or thawed, so relinquish it.
                    changed = True
                    continue
                if (len(current) != len(identities)
                        or not _exclusive_managed_unit(
                            entry["group_key"], entry["unit"], identities,
                            groups_provider, source=source, uid=uid)):
                    # A reused or now-shared unit cannot be thawed safely without
                    # the same current exclusive ownership proof used by Resume.
                    retained.append(entry)
                    continue
                thawed, _output = _run_probe(
                    runner, ("systemctl", "--user", "thaw", entry["unit"]))
                if thawed:
                    changed = True
                else:
                    retained.append(entry)
                continue
            remaining = []
            for identity in identities:
                if not _current_user_identity(identity, source=source):
                    changed = True
                    continue
                state = _process_state(identity, source=source)
                if not state:
                    # Unreadable proc state is uncertainty, not an external resume.
                    remaining.append(_identity_data(identity))
                    continue
                if not _is_stopped_state(state):
                    # The process was resumed by another actor; relinquish it.
                    changed = True
                    continue
                try:
                    result = send_signal(identity, signal.SIGCONT)
                    if result is False:
                        remaining.append(_identity_data(identity))
                    else:
                        changed = True
                except OSError:
                    remaining.append(_identity_data(identity))
            if remaining:
                retained.append(dict(entry, identities=remaining))
            continue
        restored, entry_changed = _restore_resource_entry(entry, runner, source)
        changed |= entry_changed
        if restored is not None:
            retained.append(restored)
    if changed:
        try:
            persisted = replace_entries(retained)
        except Exception:
            persisted = False
        if persisted is False:
            return (Capability(
                "journal", "unavailable", "Runtime journal persistence pending",
            ),)
    return (Capability(
        "journal", "full" if not retained else "unavailable",
        "Runtime journal restored" if not retained else "Runtime journal restore pending",
    ),)


def _action_allowed(group):
    return group.managed and group.role not in (
        Role.PRIMARY, Role.MEETING, Role.PROTECTED,
    )


@dataclass(frozen=True)
class PausedGroup:
    group_key: str
    unit: str
    identities: Tuple[ProcessIdentity, ...]
    backend: str
    pause_token: str = ""


class AppController:
    """Manual application controls with identity checks and a one-way force gate."""

    def __init__(self, runner, uid=os.getuid(), source=None, send_signal=None,
                 desktop_close=None, clock=None, sleeper=None, journal=None,
                 journal_path=None, lease=None, groups_provider=None):
        self.runner = runner
        self.uid = uid
        self.source = source
        self.send_signal = send_signal or self._os_signal
        self.desktop_close = desktop_close
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep
        self.journal = journal or RuntimeJournal(journal_path)
        self.lease = lease if lease is not None else GuardLease()
        self.groups_provider = groups_provider
        self.paused = {}
        self._deep_focus_tokens = {}
        self._deep_focus_active = False
        self._end_tokens = {}

    def _run(self, command):
        return _run_probe(self.runner, tuple(command))

    def _current(self, identity):
        return _current_user_identity(identity, source=self.source, uid=self.uid)

    def _os_signal(self, identity, signum):
        os.kill(identity.pid, signum)
        return True

    def _group_identities(self, group, require_running=False):
        identities = []
        for process in group.processes:
            if process.uid != self.uid or not self._current(process.identity):
                continue
            state = _process_state(process.identity, self.source)
            if require_running and (not state or _is_stopped_state(state)):
                continue
            identities.append(process.identity)
        return tuple(identities)

    def _known_groups(self, known_groups=None):
        if known_groups is not None:
            return tuple(known_groups)
        if self.groups_provider is None:
            return ()
        try:
            return tuple(self.groups_provider())
        except Exception:
            return ()

    def _exact_unit(self, group, identities, known_groups=None):
        return (
            bool(group.unit)
            and not group.unit.startswith("-")
            and "/" not in group.unit
            and group.unit.endswith((".scope", ".service"))
            and len(identities) == len(group.processes)
            and self._exclusive_unit(group, identities, known_groups)
        )

    def _exclusive_unit(self, group, identities, known_groups):
        if self.groups_provider is None:
            return False
        # A caller's display snapshot can be stale; only the current discovery
        # provider may authorize a unit-wide transition.
        del known_groups
        return _exclusive_managed_unit(
            group.key, group.unit, identities, self.groups_provider,
            source=self.source, uid=self.uid)

    def _exclusive_paused_unit(self, paused):
        return _exclusive_managed_unit(
            paused.group_key, paused.unit, paused.identities, self.groups_provider,
            source=self.source, uid=self.uid)

    def _unit_freezer_state(self, unit):
        ok, output = self._run(("systemctl", "--user", "show", unit,
                                "--property=FreezerState"))
        if not ok:
            return ""
        for line in output.splitlines():
            name, separator, value = line.partition("=")
            if name == "FreezerState" and separator:
                return value.strip()
        return ""

    def _denied(self, action, group):
        if _action_allowed(group):
            return None
        protected = group.role in (Role.PRIMARY, Role.MEETING, Role.PROTECTED)
        detail = (
            "Protected groups cannot be %sd" % action
            if protected else "Only managed application groups can be %sd" % action
        )
        return ActionResult(action, group.key, 0, 0, protected, detail)

    def _signal(self, identity, signum):
        if not self._current(identity):
            return False
        try:
            return self.send_signal(identity, signum) is not False
        except OSError:
            return False

    def _sync_pause_journal(self):
        return self._write_pause_records(self.paused)

    def _write_pause_records(self, records):
        entries = []
        for paused in records.values():
            entries.append({
                "action": "pause",
                "group_key": paused.group_key,
                "identities": [_identity_data(identity) for identity in paused.identities],
                "unit": paused.unit,
                "backend": paused.backend,
                "original": {"state": "running"},
                "owned": {"state": "frozen" if paused.backend in (
                    "systemd", "systemd-uncertain") else "stopped"},
            })
        try:
            return self.journal.replace_action("pause", entries) is not False
        except Exception:
            return False

    def _track(self, identities, backend, unit):
        tracked = []
        for identity in identities:
            try:
                if self.lease.track(identity, backend=backend, unit=unit) is False:
                    self._untrack(tracked)
                    return False
            except (AttributeError, OSError):
                self._untrack(tracked)
                return False
            tracked.append(identity)
        return True

    def _untrack(self, identities):
        for identity in identities:
            try:
                self.lease.untrack(identity)
            except (AttributeError, OSError):
                pass

    def _record_pause(self, group, identities, backend):
        return PausedGroup(
            group.key, group.unit, tuple(identities), backend,
            secrets.token_urlsafe(24),
        )

    def _prepare_pause(self, record):
        if not self._track(record.identities, record.backend, record.unit):
            return False
        pending = dict(self.paused)
        pending[record.group_key] = record
        if self._write_pause_records(pending):
            return True
        self._untrack(record.identities)
        return False

    def _abandon_prepared_pause(self, record):
        self._untrack(record.identities)
        self._sync_pause_journal()

    def pause(self, group, known_groups=None):
        denied = self._denied("pause", group)
        if denied is not None:
            return denied
        if group.key in self.paused:
            return ActionResult("pause", group.key, 0, 0, detail="Group is already paused")
        identities = self._group_identities(group, require_running=True)
        if not identities:
            return ActionResult("pause", group.key, 0, 0, detail="No current identities to pause")
        backend = "process"
        valid_unit = (
            bool(group.unit)
            and not group.unit.startswith("-")
            and "/" not in group.unit
            and group.unit.endswith((".scope", ".service"))
        )
        if valid_unit:
            freezer_state = self._unit_freezer_state(group.unit)
            if freezer_state in ("frozen", "freezing"):
                return ActionResult("pause", group.key, 0, 0,
                                    detail="Unit is already frozen by another actor")
            if freezer_state != "running":
                return ActionResult("pause", group.key, 0, 0,
                                    detail="Unit freezer state is unavailable")
            if self._exact_unit(group, identities, known_groups):
                # Persist intent as uncertain before the non-transactional
                # freezer request.  Only its successful acknowledgement is
                # promoted to controller-owned recovery state.
                backend = "systemd-uncertain"
        record = self._record_pause(group, identities, backend)
        if not self._prepare_pause(record):
            return ActionResult("pause", group.key, 0, 0,
                                detail="Pause guard or journal registration unavailable")
        if backend == "systemd-uncertain":
            frozen, _output = self._run(("systemctl", "--user", "freeze", group.unit))
            if frozen:
                confirmed = PausedGroup(
                    record.group_key, record.unit, record.identities,
                    "systemd", record.pause_token)
                self.paused[group.key] = confirmed
                if not self._sync_pause_journal():
                    return ActionResult("pause", group.key, 0, len(identities),
                                        detail="Unit freeze journal confirmation pending")
                return ActionResult("pause", group.key, len(identities), 0, detail="Paused")
            # A timed-out/failed systemctl request is not proof that the freezer
            # stayed running. Keep an uncertain marker unless a fresh probe
            # proves no unit-wide transition occurred.
            if self._unit_freezer_state(group.unit) == "running":
                self._abandon_prepared_pause(record)
                return ActionResult("pause", group.key, 0, 0,
                                    detail="Unit freeze unavailable")
            self.paused[group.key] = record
            return ActionResult("pause", group.key, 0, len(identities),
                                detail="Unit freeze recovery pending")
        changed = []
        for identity in identities:
            state = _process_state(identity, self.source)
            if (not self._current(identity) or not state or _is_stopped_state(state)
                    or not self._signal(identity, signal.SIGSTOP)):
                rollback_pending = tuple(
                    paused_identity for paused_identity in changed
                    if not self._signal(paused_identity, signal.SIGCONT)
                )
                if rollback_pending:
                    retained = PausedGroup(
                        record.group_key, record.unit, rollback_pending,
                        record.backend, record.pause_token)
                    self.paused[group.key] = retained
                    self._untrack(tuple(
                        tracked for tracked in record.identities
                        if tracked not in rollback_pending
                    ))
                    journal_synced = self._sync_pause_journal()
                    detail = "Pause rollback pending"
                    if not journal_synced:
                        detail += "; journal persistence pending"
                    return ActionResult("pause", group.key, 0, len(rollback_pending),
                                        detail=detail)
                self._abandon_prepared_pause(record)
                return ActionResult("pause", group.key, 0, 0,
                                    detail="Pause could not establish exact ownership")
            changed.append(identity)
        self.paused[group.key] = record
        return ActionResult("pause", group.key, len(changed), 0, detail="Paused")

    def resume(self, group_key):
        paused = self.paused.get(group_key)
        if paused is None:
            return ActionResult("resume", group_key, 0, 0, detail="Group is not paused by this controller")
        current = tuple(identity for identity in paused.identities if self._current(identity))
        changed = ()
        removed = tuple(identity for identity in paused.identities if identity not in current)
        if paused.backend in ("systemd", "systemd-uncertain") and not current:
            self._untrack(paused.identities)
            self.paused.pop(group_key, None)
            journal_synced = self._sync_pause_journal()
            if self._deep_focus_tokens.get(group_key) == paused.pause_token:
                self._deep_focus_tokens.pop(group_key, None)
            return ActionResult(
                "resume", group_key, 0, 0,
                detail=("Stale unit pause discarded" if journal_synced
                        else "Stale unit pause discarded; journal persistence pending"),
            )
        if (paused.backend == "systemd" and len(current) == len(paused.identities)
                and self._exclusive_paused_unit(paused)):
            thawed, _output = self._run(("systemctl", "--user", "thaw", paused.unit))
            if thawed:
                changed = current
            else:
                # A cgroup freezer is not undone by SIGCONT; preserve ownership.
                return ActionResult("resume", group_key, 0, len(paused.identities),
                                    detail="Unit thaw pending")
        elif paused.backend == "systemd":
            # Cannot prove an old unit is still the same application. Keep the
            # durable unit transition rather than waking a potentially reused unit.
            return ActionResult("resume", group_key, 0, len(paused.identities),
                                detail="Unit thaw identity proof pending")
        elif paused.backend == "systemd-uncertain":
            return ActionResult("resume", group_key, 0, len(paused.identities),
                                detail="Unit freeze ownership is uncertain")
        else:
            resumed = []
            for identity in current:
                state = _process_state(identity, self.source)
                if not state:
                    continue
                elif not _is_stopped_state(state):
                    removed += (identity,)
                elif self._signal(identity, signal.SIGCONT):
                    resumed.append(identity)
                else:
                    continue
            changed = tuple(resumed)
        remaining = tuple(identity for identity in paused.identities
                          if identity not in changed and identity not in removed)
        self._untrack(tuple(identity for identity in paused.identities
                            if identity not in remaining))
        if remaining:
            self.paused[group_key] = PausedGroup(
                paused.group_key, paused.unit, remaining, paused.backend, paused.pause_token)
        else:
            self.paused.pop(group_key, None)
        journal_synced = self._sync_pause_journal()
        if self._deep_focus_tokens.get(group_key) == paused.pause_token and not remaining:
            self._deep_focus_tokens.pop(group_key, None)
        detail = "Resumed" if changed else "No current identities were resumed"
        if paused.backend == "process" and any(
                not _process_state(identity, self.source) for identity in remaining):
            detail = "Process state pending"
        if not journal_synced:
            detail = "%s; journal persistence pending" % detail
        return ActionResult("resume", group_key, len(changed), len(remaining),
                            detail=detail)

    def set_deep_focus(self, groups, enabled):
        if enabled:
            if not self._deep_focus_active:
                self._deep_focus_tokens = {}
                self._deep_focus_active = True
            results = []
            for group in groups:
                if group.role != Role.BACKGROUND:
                    continue
                already_paused = group.key in self.paused
                result = self.pause(group, known_groups=groups)
                results.append(result)
                if not already_paused and group.key in self.paused:
                    self._deep_focus_tokens[group.key] = self.paused[group.key].pause_token
            return tuple(results)
        results = []
        for key, token in tuple(self._deep_focus_tokens.items()):
            paused = self.paused.get(key)
            if paused is not None and paused.pause_token == token:
                results.append(self.resume(key))
        self._deep_focus_tokens.clear()
        self._deep_focus_active = False
        return tuple(results)

    def restore_interrupted(self):
        """Recover this controller's journal through its injected safe backends."""
        result = restore_from_journal(
            runner=self.runner,
            source=self.source,
            journal=self.journal,
            send_signal=self._signal,
            groups_provider=self.groups_provider,
            uid=self.uid,
        )
        current, _capability = self.journal.load()
        restored = {}
        for entry in current["entries"]:
            if entry["action"] != "pause":
                continue
            identities = tuple(_identity_from_data(value) for value in entry["identities"])
            restored[entry["group_key"]] = PausedGroup(
                entry["group_key"], entry["unit"], identities, entry["backend"])
        self.paused = restored
        self._deep_focus_tokens.clear()
        self._deep_focus_active = False
        return result

    def _wait_for_survivors(self, identities):
        deadline = self.clock() + 3.0
        while True:
            survivors = tuple(identity for identity in identities if self._current(identity))
            if not survivors or self.clock() >= deadline:
                return survivors
            self.sleeper(min(0.05, max(0.0, deadline - self.clock())))

    def end(self, group):
        denied = self._denied("end", group)
        if denied is not None:
            return denied
        identities = self._group_identities(group)
        if not identities:
            return ActionResult("end", group.key, 0, 0, detail="No current identities to end")
        changed = ()
        if group.desktop_id and self.desktop_close is not None:
            try:
                requested = self.desktop_close(group)
                if requested is not False:
                    changed = identities
            except Exception:
                pass
        if not changed:
            changed = tuple(
                identity for identity in identities if self._signal(identity, signal.SIGTERM)
            )
        survivors = self._wait_for_survivors(identities)
        end_token = ""
        if survivors:
            for token, ticket in tuple(self._end_tokens.items()):
                if ticket[0] == group.key:
                    del self._end_tokens[token]
            end_token = secrets.token_urlsafe(24)
            self._end_tokens[end_token] = (group.key, survivors)
        detail = (
            "Graceful end requested; survivors require Force confirmation"
            if survivors else "Graceful end completed"
        )
        return ActionResult("end", group.key, len(changed), len(survivors),
                            detail=detail, end_token=end_token)

    def force(self, group, end_token):
        denied = self._denied("force", group)
        if denied is not None:
            return denied
        ticket = self._end_tokens.get(end_token)
        if ticket is None or ticket[0] != group.key:
            return ActionResult(
                "force", group.key, 0, 0,
                detail="Use graceful End and its current confirmation token first",
            )
        del self._end_tokens[end_token]
        group_identities = {
            process.identity for process in group.processes if process.uid == self.uid
        }
        targets = tuple(
            identity for identity in ticket[1]
            if identity in group_identities and self._current(identity)
        )
        changed = tuple(
            identity for identity in targets if self._signal(identity, signal.SIGKILL)
        )
        return ActionResult("force", group.key, len(changed), len(targets) - len(changed),
                            detail="Force requested" if changed else "No matching identities remain")


class GuardLease:
    """Own the pause guard process used to undo pauses after a client crash."""

    def __init__(self, script_path=None, popen_factory=None, environment=None):
        self.script_path = script_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "optimizer_guard.py")
        self.popen_factory = popen_factory or subprocess.Popen
        self.environment = environment
        self.process = None

    def _start(self):
        if self.process is not None and self.process.poll() is None:
            return True
        try:
            self.process = self.popen_factory(
                (sys.executable, self.script_path), stdin=subprocess.PIPE, text=True,
                env=self.environment)
            return self.process.stdin is not None
        except OSError:
            self.process = None
            return False

    def _write(self, message):
        if not self._start():
            return False
        try:
            self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            return False

    def track(self, identity, backend="process", unit=""):
        """Register an exact pause identity before the controller mutates it."""
        if backend not in ("process", "systemd", "systemd-uncertain") or not isinstance(unit, str):
            return False
        return self._write(dict(
            {"op": "track", "backend": backend, "unit": unit},
            **_identity_data(identity),
        ))

    def untrack(self, identity):
        return self._write(dict({"op": "untrack"}, **_identity_data(identity)))

    def close(self):
        if self.process is None:
            return
        self._write({"op": "close"})
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
            self.process.wait(timeout=3)
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            self.process = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
