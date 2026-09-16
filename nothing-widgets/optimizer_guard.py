#!/usr/bin/env python3
"""Recover optimizer-paused processes if the UI lease ends unexpectedly."""
import json
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import optimizer_core as optimizer_core


def _identity_from_message(message):
    if not isinstance(message, dict) or not {"op", "pid", "start_time"} <= set(message):
        return None
    value = {"pid": message["pid"], "start_time": message["start_time"]}
    if not optimizer_core._valid_identity_data(value):
        return None
    return optimizer_core.ProcessIdentity(**value)


def _live_groups_provider():
    """Return a fresh, current-user classification for unit recovery proof."""
    uid = os.getuid()
    return optimizer_core.classify_groups(
        optimizer_core.read_processes(uid=uid), uid=uid)


def main(stdin=None, groups_provider=None, restore_from_journal=None):
    """Read a lease stream and always restore exact tracked identities at its end."""
    tracked = {}
    stdin = sys.stdin if stdin is None else stdin
    groups_provider = groups_provider or _live_groups_provider
    restore_from_journal = restore_from_journal or optimizer_core.restore_from_journal
    try:
        for line in stdin:
            try:
                message = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(message, dict):
                continue
            operation = message.get("op")
            if operation == "close" and set(message) == {"op"}:
                break
            identity = _identity_from_message(message)
            if identity is None:
                continue
            if operation == "track":
                if set(message) not in ({"op", "pid", "start_time"},
                                        {"op", "pid", "start_time", "backend", "unit"}):
                    continue
                backend = message.get("backend", "process")
                unit = message.get("unit", "")
                if backend not in (
                        "process", "process-prepared", "systemd", "systemd-uncertain") \
                        or not isinstance(unit, str):
                    continue
                tracked[identity] = (backend, unit)
            elif operation == "untrack" and set(message) == {"op", "pid", "start_time"}:
                tracked.pop(identity, None)
    finally:
        for identity, (backend, _unit) in tracked.items():
            if backend != "process" or not optimizer_core._current_user_identity(identity):
                continue
            if not optimizer_core._is_stopped_state(optimizer_core._process_state(identity)):
                continue
            try:
                os.kill(identity.pid, signal.SIGCONT)
            except OSError:
                pass
        restore_from_journal(groups_provider=groups_provider, uid=os.getuid())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
