#!/usr/bin/env python3
"""Display-independent domain models and policies for AGENT WORK."""
import dataclasses
import json
import os
import tempfile
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
        certain=True,
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
        certain=True,
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
    helper_ok, _helper_output = _run_probe(
        runner, ("nothing-optimizer-helper", "--capabilities")
    )
    power_ok, _power_output = _run_probe(runner, ("powerprofilesctl", "get"))
    network = (
        Capability("network", "full", "Privileged network helper is available")
        if helper_ok else
        Capability("network", "fallback", "BASIC network policy only; helper unavailable")
    )
    power = (
        Capability("power", "full", "power-profiles service is available")
        if power_ok else
        Capability("power", "unavailable", "power-profiles service is unavailable")
    )
    return network, power
