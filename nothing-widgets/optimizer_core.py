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
