import json
import os

from guardian.fsutil import dir_size
from guardian.probes.scan import (dangling_images, deep_scan, docker_build_cache, journal_size, rotated_logs,
                                  top_dirs)
from tests.helpers import Recorder


def test_dir_size_counts_files_and_ignores_dir_symlinks(tmp_path):
    (tmp_path / "a").write_bytes(b"x" * 100)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_bytes(b"y" * 50)
    (tmp_path / "link").symlink_to("/usr")
    assert dir_size(tmp_path) == 150


def test_dir_size_missing_path_is_zero(tmp_path):
    assert dir_size(tmp_path / "nope") == 0


def test_docker_build_cache_reads_build_cache_row():
    out = "\n".join([json.dumps({"Type": "Images", "Reclaimable": "6.82GB (50%)"}),
                     json.dumps({"Type": "Build Cache", "Reclaimable": "5.69GB"})])
    assert docker_build_cache(Recorder({"system df": (out, 0)})) == 5_690_000_000


def test_docker_helpers_survive_missing_docker():
    rec = Recorder(exc=FileNotFoundError("docker"))
    assert docker_build_cache(rec) == 0
    assert dangling_images(rec) == 0


def test_dangling_images_counts_ids():
    assert dangling_images(Recorder({"dangling": ("aaa\nbbb\n\n", 0)})) == 2


def test_journal_size_parses_binary_suffix():
    out = "Archived and active journals take up 651.6M in the file system.\n"
    assert journal_size(Recorder({"journalctl": (out, 0)})) == round(651.6 * 2**20)
    assert journal_size(Recorder({"journalctl": ("weird", 0)})) == 0


def test_rotated_logs_counts_only_old_rotated_files(tmp_path):
    now = 1_000_000_000.0
    for name in ("old.gz", "new.gz", "old.log", "syslog.1"):
        (tmp_path / name).write_bytes(b"z" * 10)
    old = now - 8 * 86400
    for name in ("old.gz", "old.log", "syslog.1"):
        os.utime(tmp_path / name, (old, old))
    os.utime(tmp_path / "new.gz", (now, now))
    assert rotated_logs(tmp_path, now) == 20


def test_top_dirs_parses_du_and_skips_home(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    out = f"300\t{tmp_path/'a'}\n200\t{tmp_path/'b'}\n1000\t{tmp_path}\ngarbage\n"
    got = top_dirs(tmp_path, Recorder({"du ": (out, 1)}))  # du exits 1 on permission errors
    assert [(d.path, d.size_bytes) for d in got] == [(str(tmp_path / "a"), 300), (str(tmp_path / "b"), 200)]


def test_deep_scan_collects_user_caches(tmp_path):
    pip = tmp_path / ".cache" / "pip"
    pip.mkdir(parents=True)
    (pip / "f").write_bytes(b"x" * 10)
    scan = deep_scan(tmp_path, run=Recorder(), now=5.0, apt_dir=tmp_path / "apt", log_dir=tmp_path / "log")
    assert scan.at == 5.0
    assert scan.reclaim.user_caches == {"pip": 10}
    assert scan.reclaim.build_cache == 0 and scan.reclaim.apt_cache == 0
