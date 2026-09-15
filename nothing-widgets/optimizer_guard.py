#!/usr/bin/env python3
"""Recover optimizer-paused processes if the UI lease ends unexpectedly."""
import json
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import optimizer_core as optimizer_core


def _identity_from_message(message):
    if not isinstance(message, dict) or set(message) != {"op", "pid", "start_time"}:
        return None
    value = {"pid": message["pid"], "start_time": message["start_time"]}
    if not optimizer_core._valid_identity_data(value):
        return None
    return optimizer_core.ProcessIdentity(**value)


def main(stdin=None):
    """Read a lease stream and always restore exact tracked identities at its end."""
    tracked = set()
    stdin = sys.stdin if stdin is None else stdin
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
                tracked.add(identity)
            elif operation == "untrack":
                tracked.discard(identity)
    finally:
        for identity in tracked:
            if not optimizer_core._current_user_identity(identity):
                continue
            try:
                os.kill(identity.pid, signal.SIGCONT)
            except OSError:
                pass
        optimizer_core.restore_from_journal()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
