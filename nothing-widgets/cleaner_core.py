#!/usr/bin/env python3
"""Cleaner core — disk reclaim + memory visibility, with no GTK in sight.

Everything the CLEAN card does to the machine lives here: sizing caches, deleting
them, sampling processes, and bringing back anything a clean knocked over. Kept
separate from nothing-widgets.py so the file-deleting half can be tested without a
display, and so the widget file stays a rendering file.
"""
import json, os, signal, subprocess, time

HOME     = os.path.expanduser("~")
CONF_DIR = os.path.join(HOME, ".config/nothing-widgets")
HIST_FILE = os.path.join(CONF_DIR, "cleaner-history.jsonl")

# Deletion is only ever allowed inside these roots. The guard below is the single
# checkpoint every path passes through — see guard() for why it is not optional.
ALLOWED_ROOTS = (os.path.join(HOME, ".cache"),
                 os.path.join(HOME, ".local/share/Trash"))

HISTORY_DAYS = 7
SAMPLE_TOP_N = 8

# ---------- shell ----------
def _run(cmd, timeout=90):
    """Run a command list, never raise. -> (ok, output)"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, (p.stdout + p.stderr).strip()
    except Exception as e:
        return False, str(e)

# ---------- targets ----------
class Target:
    """One cleanable thing. `paths` are emptied; `cmd` is run instead if given.

    held   — shown and sized, but never touched by CLEAN NOW (only its own button)
    root   — needs the pinned sudoers rule; renders AUTH when that is missing
    service— a systemd --user unit that owns the path, stopped around the delete
    """
    def __init__(self, key, label, paths=(), cmd=None, size_cmd=None,
                 root=False, held=False, service=None, floor=0):
        self.key, self.label = key, label
        self.paths = [os.path.expanduser(p) for p in paths]
        self.cmd, self.size_cmd = cmd, size_cmd
        self.root, self.held, self.service = root, held, service
        self.floor = floor      # bytes the command leaves behind, never reclaimable

JOURNAL_KEEP = 100 * 1024 * 1024

TARGETS = [
    # Sized at archives/ only: `apt-get clean` empties that directory, it does not
    # touch pkgcache.bin — sizing all of /var/cache/apt would promise ~120 MB that
    # the button cannot deliver.
    Target("apt", "APT", cmd=["/usr/bin/apt-get", "clean"],
           size_cmd="/var/cache/apt/archives", root=True),
    # Vacuum keeps the most recent 100 MB, so that much is never reclaimable.
    Target("journal", "JOURNAL",
           cmd=["/usr/bin/journalctl", "--vacuum-size=100M"],
           size_cmd="/var/log/journal", root=True, floor=JOURNAL_KEEP),
    Target("brave", "BRAVE", paths=["~/.cache/BraveSoftware"]),
    Target("vicinae", "VICINAE", paths=["~/.cache/vicinae"], service="vicinae.service"),
    Target("mesa", "MESA", paths=["~/.cache/mesa_shader_cache"]),
    Target("pip", "PIP", paths=["~/.cache/pip"]),
    Target("mintinstall", "MINTINSTALL", paths=["~/.cache/mintinstall"]),
    Target("flatpak", "FLATPAK", paths=["~/.cache/flatpak"]),
    Target("thumbs", "THUMBNAILS", paths=["~/.cache/thumbnails"]),
    Target("trash", "TRASH", paths=["~/.local/share/Trash/files",
                                    "~/.local/share/Trash/info"]),
    # Held: real space, real cost to remove. Puppeteer re-downloads ~600 MB of
    # browsers on the next run; uv loses every wheel it has cached.
    Target("puppeteer", "PUPPETEER", paths=["~/.cache/puppeteer"], held=True),
    Target("uv", "UV", paths=["~/.cache/uv"], held=True),
]

BY_KEY = {t.key: t for t in TARGETS}

# ---------- the path guard ----------
class UnsafePath(Exception):
    """Raised before any unlink when a path escapes ALLOWED_ROOTS."""

def guard(path):
    """Resolve `path` and prove it sits inside an allowed root.

    Resolving first is the point: a symlink at ~/.cache/foo pointing at ~/projects
    must be rejected, not followed. Returns the resolved path; raises otherwise.
    """
    real = os.path.realpath(path)
    for root in ALLOWED_ROOTS:
        rroot = os.path.realpath(root)
        if real == rroot or real.startswith(rroot + os.sep):
            return real
    raise UnsafePath(path)

# ---------- sizing ----------
def du_bytes(path, _budget=400000):
    """Apparent size of a tree, via scandir. Never raises; caps the walk so a
    pathological directory cannot stall the scan thread."""
    total = 0
    stack = [path]
    seen = 0
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    seen += 1
                    if seen > _budget:
                        return total
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total

SUDOERS_RULE = "/etc/sudoers.d/nothing-cleaner"

def sudo_ok():
    """True when the pinned NOPASSWD rule is installed.

    Tests for the rule file rather than asking sudo. `sudo -n -l` answers a
    different question — it says yes for a user with a general `ALL=(ALL) ALL`
    entry whose credentials merely happen to be cached — so the card would size
    apt and the journal as reclaimable and then fail to clean them once the sudo
    timestamp lapsed. The file is the thing that makes a silent clean possible, so
    the file is what we check. Readable without root: /etc/sudoers.d is 0755.
    """
    return os.path.exists(SUDOERS_RULE)

def scan():
    """Size every target. -> list of dicts the widget renders directly."""
    auth = sudo_ok()
    rows = []
    for t in TARGETS:
        row = {"key": t.key, "label": t.label, "held": t.held,
               "root": t.root, "bytes": 0, "state": "ok"}
        if t.root and not auth:
            # Still size it. The point of the AUTH row is to say "there is 1.5 GB
            # here and one install step away from being reclaimable" — a row with
            # no number tells the user nothing. reclaimable() and auto_keys()
            # filter on state, so this never inflates the advertised total.
            row["state"] = "auth"
        try:
            if t.size_cmd:
                raw = du_bytes(t.size_cmd)
            else:
                raw = sum(du_bytes(p) for p in t.paths if os.path.isdir(p))
            # Report what cleaning would actually free, not what is on disk.
            row["bytes"] = max(0, raw - t.floor)
        except Exception:
            row["state"] = "err"
        rows.append(row)
    return rows

def reclaimable(rows):
    """Total of the rows CLEAN NOW would actually free."""
    return sum(r["bytes"] for r in rows
               if not r["held"] and r["state"] == "ok")

# ---------- cleaning ----------
def _empty_dir(path):
    """Delete the CONTENTS of path, keep path itself. Apps expect their cache
    directory to exist. -> bytes freed."""
    real = guard(path)
    if not os.path.isdir(real):
        return 0
    freed = du_bytes(real)
    for name in os.listdir(real):
        child = os.path.join(real, name)
        try:
            guard(child)
        except UnsafePath:
            continue                      # a symlink out of bounds: leave it alone
        try:
            if os.path.islink(child) or os.path.isfile(child):
                os.unlink(child)
            elif os.path.isdir(child):
                _rmtree(child)
        except OSError:
            pass
    return freed

def _rmtree(path):
    """shutil.rmtree, but every directory it descends into is guarded first."""
    guard(path)
    for root, dirs, files in os.walk(path, topdown=False, followlinks=False):
        try:
            guard(root)
        except UnsafePath:
            continue
        for f in files:
            try: os.unlink(os.path.join(root, f))
            except OSError: pass
        for d in dirs:
            p = os.path.join(root, d)
            try:
                os.rmdir(p) if not os.path.islink(p) else os.unlink(p)
            except OSError: pass
    try: os.rmdir(path)
    except OSError: pass

def clean_one(key):
    """Clean a single target. Never raises. -> dict(key, freed, state)."""
    t = BY_KEY.get(key)
    if t is None:
        return {"key": key, "freed": 0, "state": "err"}
    res = {"key": key, "freed": 0, "state": "ok"}
    stopped = False
    try:
        if t.service:
            # Stop the owner first: clearing a live app's cache under it is how you
            # get a crash instead of a clean.
            stopped, _ = _run(["/usr/bin/systemctl", "--user", "stop", t.service], 30)
        if t.cmd:
            before = du_bytes(t.size_cmd) if t.size_cmd else 0
            ok, _ = _run(["/usr/bin/sudo", "-n"] + t.cmd)
            if not ok:
                res["state"] = "auth"
            else:
                after = du_bytes(t.size_cmd) if t.size_cmd else 0
                res["freed"] = max(0, before - after)
        else:
            res["freed"] = sum(_empty_dir(p) for p in t.paths if os.path.isdir(p))
    except UnsafePath:
        res["state"] = "blocked"
    except Exception:
        res["state"] = "err"
    finally:
        if stopped:
            _run(["/usr/bin/systemctl", "--user", "start", t.service], 30)
    return res

def clean(keys):
    """Clean each key independently — one failure never aborts the others."""
    return [clean_one(k) for k in keys]

def auto_keys(rows):
    """Keys CLEAN NOW touches: everything not held, not unauthorised, non-empty."""
    return [r["key"] for r in rows
            if not r["held"] and r["state"] == "ok" and r["bytes"] > 0]

# ---------- keeping apps alive ----------
# Anything here that was running before a clean and is gone afterwards gets its real
# launch command re-run. The widget itself is deliberately absent: a dead process
# cannot revive itself, so instead it is excluded from the kill list entirely.
WATCHED = {
    "vicinae": dict(comm="vicinae-server",
                    revive=["/usr/bin/systemctl", "--user", "restart", "vicinae.service"]),
    "conky":   dict(comm="conky",
                    revive=["/usr/bin/conky", "-c",
                            os.path.join(HOME, ".config/conky/nothing/bg.conf")]),
}

def _spawn(cmd):
    """Launch detached and never wait.

    The thing being revived is a daemon: conky runs until killed. Waiting on it
    blocks until the timeout and then reports failure for a process that started
    perfectly well. start_new_session detaches it so it survives this process.
    """
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except Exception:
        return False

def _alive(comm_name):
    """Exact match on the process NAME, not a substring of its command line.

    Substring matching looked tidier and was wrong: any shell that merely mentions
    'conky' — a pkill, a grep, this program's own argv — counts as conky being up,
    so the revive step decides there is nothing to bring back and the process
    stays dead. comm is exact and cannot be spoofed by a passing command line.
    """
    me = os.getpid()
    for pid, comm, _cmdline, _rss in _iter_procs():
        if pid != me and comm == comm_name:
            return True
    return False

def snapshot_watched():
    return {n: _alive(w["comm"]) for n, w in WATCHED.items()}

def revive(snapshot, wait=4.0):
    """Relaunch anything that was up before the clean and is down now.
    -> list of names confirmed running again.

    Success is judged by liveness, not by exit code: the only question that
    matters is whether the process is back, and a detached spawn has no useful
    exit code to report.
    """
    back = []
    for name, was_up in snapshot.items():
        if not was_up:
            continue
        comm = WATCHED[name]["comm"]
        if _alive(comm):
            continue
        if not _spawn(WATCHED[name]["revive"]):
            continue
        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(0.25)
            if _alive(comm):
                back.append(name)
                break
    return back

# ---------- processes ----------
PAGE = os.sysconf("SC_PAGE_SIZE")

def _iter_procs():
    """(pid, comm, cmdline, rss_bytes) for every readable process."""
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open("/proc/%d/statm" % pid) as fh:
                rss = int(fh.read().split()[1]) * PAGE
            with open("/proc/%d/comm" % pid) as fh:
                comm = fh.read().strip()
            with open("/proc/%d/cmdline" % pid, "rb") as fh:
                cmdline = fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except (OSError, ValueError, IndexError):
            continue
        yield pid, comm, cmdline, rss

def sample(top_n=SAMPLE_TOP_N):
    """Top processes by RSS, merged per program name so ten Brave renderers read as
    one 'brave' line — which is the number you actually care about."""
    agg = {}
    me = os.getpid()
    for pid, comm, _cmd, rss in _iter_procs():
        if pid == me:
            continue
        cur = agg.get(comm)
        if cur is None:
            # `pid`/`top` track the single largest process of this name — that is the
            # one KILL should target, not whichever renderer we happened to see first.
            agg[comm] = {"name": comm, "rss": rss, "pid": pid, "top": rss}
        else:
            cur["rss"] += rss
            if rss > cur["top"]:
                cur["top"], cur["pid"] = rss, pid
    return sorted(agg.values(), key=lambda r: -r["rss"])[:top_n]

def kill(pid):
    """SIGTERM only — never SIGKILL, and never this process."""
    if int(pid) == os.getpid():
        return False
    try:
        os.kill(int(pid), signal.SIGTERM)
        return True
    except OSError:
        return False

# ---------- history ----------
class History:
    """Rolling JSONL of samples. One line per tick, pruned to HISTORY_DAYS."""
    def __init__(self, path=HIST_FILE, days=HISTORY_DAYS):
        self.path, self.days = path, days

    def append(self, rows, now=None):
        now = time.time() if now is None else now
        line = json.dumps({"t": int(now),
                           "p": [[r["name"], r["rss"]] for r in rows]})
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a") as fh:
                fh.write(line + "\n")
        except OSError:
            return
        # Prune lazily: rewriting on every tick would be pointless churn.
        if int(now) % 120 < 30:
            self.prune(now)

    def load(self, now=None):
        now = time.time() if now is None else now
        cutoff = now - self.days * 86400
        out = []
        try:
            with open(self.path) as fh:
                for ln in fh:
                    try:
                        e = json.loads(ln)
                    except ValueError:
                        continue
                    if e.get("t", 0) >= cutoff:
                        out.append(e)
        except OSError:
            pass
        return out

    def prune(self, now=None):
        entries = self.load(now)
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                for e in entries:
                    fh.write(json.dumps(e) + "\n")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def series(self, buckets=12, top_n=4, now=None):
        """Per-app peak RSS and a bucketed series over the window.
        -> [{name, peak, points[buckets], pid}] sorted by peak desc."""
        now = time.time() if now is None else now
        entries = self.load(now)
        if not entries:
            return []
        span = self.days * 86400
        start = now - span
        peaks, grid = {}, {}
        for e in entries:
            b = int((e["t"] - start) / span * buckets)
            b = min(max(b, 0), buckets - 1)
            for name, rss in e.get("p", []):
                peaks[name] = max(peaks.get(name, 0), rss)
                row = grid.setdefault(name, [0] * buckets)
                row[b] = max(row[b], rss)
        live = {r["name"]: r["pid"] for r in sample(top_n=64)}
        top = sorted(peaks, key=lambda n: -peaks[n])[:top_n]
        return [{"name": n, "peak": peaks[n], "points": grid[n],
                 "pid": live.get(n)} for n in top]
