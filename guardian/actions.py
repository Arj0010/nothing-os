"""The ONLY code allowed to change the system. Every entry re-validates its target before acting."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import psutil

from .config import CACHE_PATHS, CGROUP_ROOT, HELPER
from .fsutil import dir_size
from .models import ActionResult, ActionSpec
from .probes.docker import container_usage
from .protect import is_protected
from .units import parse_size

CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
ROOT_VERBS = {"apt_clean": "apt-clean", "journal_vacuum": "journal-vacuum", "rotated_logs_clean": "rotated-logs"}
RECLAIMED = re.compile(r"Total reclaimed space:\s*([0-9.]+\s*[A-Za-z]+)")
IDLE_CPU = 1.0
RECHECK_SECS = 2.0


@dataclass
class ActionContext:
    home: Path = field(default_factory=Path.home)
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    protected: set[str] = field(default_factory=set)
    kill_grace: float = 10.0
    cgroup_root: Path = CGROUP_ROOT
    sleep: Callable[[float], None] = time.sleep


def _fail(detail: str) -> ActionResult:
    return ActionResult(ok=False, detail=detail)


def _cmd(ctx: ActionContext, args: list[str], timeout: int = 600) -> ActionResult:
    try:
        p = ctx.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return _fail(f"{args[0]} failed: {e}")
    out = (p.stdout or "").strip()
    if p.returncode != 0:
        return _fail(((p.stderr or "").strip() or out)[-300:] or f"{args[0]} exited {p.returncode}")
    return ActionResult(ok=True, detail=out[-300:])


def kill_process(spec: ActionSpec, ctx: ActionContext) -> ActionResult:
    try:
        pid = int(spec.fingerprint["pid"])
        start = float(spec.fingerprint["start_time"])
        name = str(spec.fingerprint["name"])
    except (KeyError, ValueError, TypeError):
        return _fail("refused: bad fingerprint")
    if pid <= 1 or pid == os.getpid():
        return _fail(f"refused: will not kill pid {pid}")
    try:
        p = psutil.Process(pid)
        if abs(p.create_time() - start) > 0.01 or p.name() != name:
            return _fail("refused: process changed since the scan (pid reused)")
        if is_protected(p.name(), " ".join(p.cmdline()), ctx.protected):
            return _fail(f"refused: {name} is protected")
        rss = p.memory_info().rss
        p.terminate()
        try:
            p.wait(ctx.kill_grace)
        except psutil.TimeoutExpired:
            p.kill()
            p.wait(5)
    except psutil.NoSuchProcess:
        return _fail("process already gone")
    except psutil.AccessDenied:
        return _fail(f"permission denied killing {name}")
    except psutil.TimeoutExpired:
        return _fail(f"{name} did not exit after SIGKILL")
    return ActionResult(ok=True, detail=f"killed {name} (pid {pid})", freed_bytes=rss)


def stop_container(spec: ActionSpec, ctx: ActionContext) -> ActionResult:
    cid = spec.target or ""
    if not CONTAINER_ID.match(cid):
        return _fail(f"refused: bad container id {cid!r}")
    before = container_usage(cid, ctx.cgroup_root)
    ctx.sleep(RECHECK_SECS)
    after = container_usage(cid, ctx.cgroup_root)
    if before is None or after is None:
        return _fail(f"container {cid[:12]} is not running")
    cpu = (after[0] - before[0]) / (RECHECK_SECS * 1e6) * 100
    if cpu >= IDLE_CPU:
        return _fail(f"refused: container busy ({cpu:.1f}% CPU right now)")
    return _cmd(ctx, ["docker", "stop", cid], timeout=60)


def clear_user_cache(spec: ActionSpec, ctx: ActionContext) -> ActionResult:
    rel = CACHE_PATHS.get(spec.target or "")
    if rel is None:
        return _fail(f"refused: unknown cache {spec.target!r}")
    path = ctx.home / rel
    if path.is_symlink():
        return _fail(f"refused: {path} is a symlink")
    if not path.exists():
        return ActionResult(ok=True, detail=f"{spec.target} cache already empty")
    size = dir_size(path)
    try:
        shutil.rmtree(path)
    except OSError as e:
        return _fail(f"could not clear {path}: {e}")
    return ActionResult(ok=True, detail=f"cleared {path}", freed_bytes=size)


def _docker_prune(args: list[str]):
    def act(spec: ActionSpec, ctx: ActionContext) -> ActionResult:
        r = _cmd(ctx, args)
        m = RECLAIMED.search(r.detail)
        if r.ok and m:
            try:
                r.freed_bytes = parse_size(m.group(1).replace(" ", ""))
            except ValueError:
                pass
        return r
    return act


def _root(verb: str):
    return lambda spec, ctx: _cmd(ctx, ["pkexec", HELPER, verb])


REGISTRY: dict[str, Callable[[ActionSpec, ActionContext], ActionResult]] = {
    "kill_process": kill_process,
    "stop_container": stop_container,
    "clear_user_cache": clear_user_cache,
    "docker_builder_prune": _docker_prune(["docker", "builder", "prune", "-af"]),
    "docker_image_prune_dangling": _docker_prune(["docker", "image", "prune", "-f"]),
    **{name: _root(verb) for name, verb in ROOT_VERBS.items()},
}


def execute(spec: ActionSpec, ctx: ActionContext) -> ActionResult:
    fn = REGISTRY.get(spec.name)
    if fn is None:
        return _fail(f"refused: unknown action {spec.name!r}")
    try:
        return fn(spec, ctx)
    except Exception as e:  # noqa: BLE001 - an action must never crash the caller
        return _fail(f"error in {spec.name}: {e}")
