#!/usr/bin/env python3
"""Display-independent domain models and policies for AGENT WORK."""
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


def _has_stable_identity(process):
    return process.identity.pid > 0 and process.identity.start_time > 0


def _has_stable_cgroup(process):
    return bool(process.cgroup and process.cgroup.strip() not in ("", "/"))


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
    names = {_executable_name(process) for process in processes}
    if names & PROTECTED_EXECUTABLES:
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


def _unmanaged_group(process, reason):
    label, role = _label_and_role((process,))
    return AppGroup(
        key="process-%d-%d" % (process.identity.pid, process.identity.start_time),
        label=label,
        role=role,
        processes=(process,),
        managed=False,
        reason=reason,
    )


def _agent_roots(processes):
    by_pid = {process.identity.pid: process for process in processes}
    candidates = {
        process.identity.pid for process in processes
        if _executable_name(process) in AGENT_EXECUTABLES
    }
    roots = []
    for candidate in sorted(candidates):
        parent = by_pid[candidate].ppid
        ancestors = set()
        while parent in by_pid and parent not in ancestors:
            if parent in candidates:
                break
            ancestors.add(parent)
            parent = by_pid[parent].ppid
        else:
            roots.append(candidate)
    return tuple(roots)


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

    for root_pid in _agent_roots(stable):
        member_pids = descendants(stable, (root_pid,))
        members = tuple(
            process for process in stable
            if process.identity.pid in member_pids
        )
        assigned.update(process.identity for process in members)
        root = next(process for process in members if process.identity.pid == root_pid)
        role = (Role.PROTECTED if any(
            _executable_name(process) in PROTECTED_EXECUTABLES for process in members
        ) else Role.PRIMARY)
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
        if not _has_stable_identity(process):
            groups.append(_unmanaged_group(process, "unresolved process identity"))
            assigned.add(process.identity)

    pending = tuple(process for process in stable if process.identity not in assigned)
    by_cgroup = {}
    for process in pending:
        if _has_stable_cgroup(process):
            by_cgroup.setdefault(process.cgroup, []).append(process)
        else:
            groups.append(_unmanaged_group(process, "missing stable cgroup"))

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
