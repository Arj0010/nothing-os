import os

from guardian.probes.disk import probe_disk
from guardian.probes.docker import container_usage, probe_containers
from guardian.probes.ram import probe_ram, read_psi
from tests.helpers import Recorder, fake_cgroup

CID = "a" * 64


def test_probe_containers_reads_cgroup_not_docker_stats(tmp_path):
    fake_cgroup(tmp_path, CID, mem=243_000_000, usec=5_000_000)
    rec = Recorder({"docker ps": (f"{CID} memobase-api\n{'b' * 64} gone-already\n", 0)})
    [c] = probe_containers(rec, root=tmp_path)  # second container has no cgroup dir -> skipped
    assert (c.id, c.name, c.mem_bytes, c.cpu_usec) == (CID, "memobase-api", 243_000_000, 5_000_000)
    assert not any("stats" in part for call in rec.calls for part in call)


def test_probe_containers_docker_missing_or_down(tmp_path):
    assert probe_containers(Recorder(exc=FileNotFoundError("docker")), root=tmp_path) == []
    assert probe_containers(Recorder({"docker ps": ("", 1)}), root=tmp_path) == []


def test_container_usage_missing_or_garbled(tmp_path):
    assert container_usage(CID, tmp_path) is None
    d = fake_cgroup(tmp_path, CID)
    (d / "cpu.stat").write_text("nonsense\n")
    assert container_usage(CID, tmp_path) is None


def test_read_psi_parses_avg60(tmp_path):
    f = tmp_path / "memory"
    f.write_text("some avg10=1.00 avg60=12.50 avg300=3.00 total=1\nfull avg10=0.50 avg60=6.25 avg300=1.00 total=1\n")
    assert read_psi(f) == (12.5, 6.25)
    assert read_psi(tmp_path / "missing") == (0.0, 0.0)


def test_read_psi_real_kernel():
    some, full = read_psi()
    assert 0.0 <= full <= some <= 100.0


def test_probe_disk_matches_df_formula(tmp_path):
    f = probe_disk(str(tmp_path))
    st = os.statvfs(tmp_path)
    assert f.avail == st.f_bavail * st.f_frsize
    assert f.used == (st.f_blocks - st.f_bfree) * st.f_frsize
    assert 0 < f.pct < 100


def test_probe_ram_real_system():
    r = probe_ram(top_n=5)
    assert r.total > 0 and 0 < r.available <= r.total
    assert len(r.top) <= 5
    rss = [p.rss for p in r.top]
    assert rss == sorted(rss, reverse=True)
    assert any(p.pid == os.getpid() for p in probe_ram(top_n=10_000).top)
