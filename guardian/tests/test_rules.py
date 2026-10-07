from guardian.models import (ContainerInfo, DeepScan, DiskFacts, Facts, History, ProcInfo, RamFacts,
                             Reclaimable, Severity)
from guardian.rules import ZERO_RISK, evaluate

GB = 10**9
NOW = 100_000.0
H6 = 6 * 3600


def facts(disk_pct=50.0, avail_pct=50.0, psi=(0.0, 0.0), top=(), containers=(), reclaim=None):
    used = int(100 * GB * disk_pct / 100)
    return Facts(
        now=NOW,
        ram=RamFacts(total=10 * GB, available=int(10 * GB * avail_pct / 100), swap_total=0, swap_free=0,
                     psi_some=psi[0], psi_full=psi[1], top=list(top)),
        disk=DiskFacts(mount="/", used=used, avail=100 * GB - used),
        containers=list(containers),
        scan=DeepScan(at=NOW, reclaim=reclaim) if reclaim is not None else None,
    )


def proc(pid=500, rss=GB, name="python3", uid=1000, start=1.0):
    return ProcInfo(pid=pid, start_time=start, name=name, cmdline=f"{name} app.py", rss=rss, uid=uid)


def ev(f, hist=None, since=None):
    return evaluate(f, hist or History(), protected=set(), critical_unanswered_since=since, my_uid=1000)


def of(findings, kind):
    return [x for x in findings if x.kind == kind]


# --- disk ---------------------------------------------------------------
def test_disk_below_80_is_quiet():
    assert ev(facts(disk_pct=79)) == []


def test_disk_80_is_info():
    [d] = of(ev(facts(disk_pct=81)), "disk_high")
    assert d.severity is Severity.info


def test_disk_86_warns_with_bundled_reclaim_actions():
    r = Reclaimable(build_cache=3 * GB, user_caches={"pip": 200_000_000, "uv": 50_000_000})
    [d] = of(ev(facts(disk_pct=86, reclaim=r)), "disk_high")
    assert d.severity is Severity.warn
    assert [(a.name, a.target) for a in d.actions] == [("docker_builder_prune", None), ("clear_user_cache", "pip")]
    assert d.est_saving_bytes == 3 * GB + 200_000_000


def test_disk_high_without_scan_points_to_review():
    [d] = of(ev(facts(disk_pct=86)), "disk_high")
    assert d.actions == [] and "guardian review" in d.reason


def test_disk_93_is_critical():
    [d] = of(ev(facts(disk_pct=93)), "disk_high")
    assert d.severity is Severity.critical


def test_emergency_after_10_unanswered_minutes():
    out = ev(facts(disk_pct=96), since=NOW - 600)
    [e] = of(out, "disk_emergency")
    assert e.severity is Severity.emergency
    assert [(a.name, a.target) for a in e.actions] == [(a.name, a.target) for a in ZERO_RISK]
    assert of(out, "disk_high")[0].severity is Severity.critical


def test_no_emergency_before_10_minutes_or_when_answered():
    assert of(ev(facts(disk_pct=96), since=NOW - 599), "disk_emergency") == []
    assert of(ev(facts(disk_pct=96), since=None), "disk_emergency") == []


def test_zero_risk_list_contains_no_kill_or_stop():
    assert {a.name for a in ZERO_RISK} == {"docker_builder_prune", "clear_user_cache", "apt_clean",
                                          "journal_vacuum", "rotated_logs_clean"}
    assert {a.target for a in ZERO_RISK if a.name == "clear_user_cache"} == {"pip", "uv", "npm"}


# --- ram (MemAvailable + kernel PSI) ------------------------------------
def test_ram_fine_is_quiet():
    assert of(ev(facts(avail_pct=50, top=[proc()])), "ram_low") == []


def test_ram_warn_targets_biggest_own_unprotected_process():
    top = [proc(pid=1, name="cinnamon", rss=3 * GB), proc(pid=2, uid=0, rss=2 * GB), proc(pid=3, rss=GB)]
    [r] = of(ev(facts(avail_pct=15, top=top)), "ram_low")
    assert r.severity is Severity.warn
    [a] = r.actions
    assert a.name == "kill_process" and a.target == "3"
    assert a.fingerprint == {"pid": 3, "start_time": 1.0, "name": "python3"}


def test_ram_critical_below_10_percent():
    assert of(ev(facts(avail_pct=8, top=[proc()])), "ram_low")[0].severity is Severity.critical


def test_psi_thrash_flags_even_when_available_looks_fine():
    # zram swapping: MemAvailable 40% but tasks stalling on memory
    [w] = of(ev(facts(avail_pct=40, psi=(12.0, 1.0), top=[proc()])), "ram_low")
    assert w.severity is Severity.warn and "stall" in w.reason
    [c] = of(ev(facts(avail_pct=40, psi=(30.0, 6.0), top=[proc()])), "ram_low")
    assert c.severity is Severity.critical


def test_ram_low_without_candidates_has_no_action():
    [r] = of(ev(facts(avail_pct=15, top=[proc(name="Xorg")])), "ram_low")
    assert r.actions == []


# --- leaks --------------------------------------------------------------
def test_leak_detected_when_growth_over_threshold_and_still_growing():
    hist = History(rss={"500:1.0": [(NOW - 1700, 0.5 * GB), (NOW - 600, 1.5 * GB)]})
    [l] = of(ev(facts(top=[proc(rss=int(2.1 * GB))]), hist), "leak_suspect")
    assert l.actions[0].name == "kill_process"


def test_no_leak_when_not_still_growing():
    hist = History(rss={"500:1.0": [(NOW - 1700, 0.5 * GB), (NOW - 600, 2.2 * GB)]})
    assert of(ev(facts(top=[proc(rss=int(2.1 * GB))]), hist), "leak_suspect") == []


def test_samples_outside_window_ignored():
    hist = History(rss={"500:1.0": [(NOW - 2000, 0.1 * GB), (NOW - 100, 1.0 * GB)]})
    assert of(ev(facts(top=[proc(rss=int(1.7 * GB))]), hist), "leak_suspect") == []


def test_protected_process_never_flagged_as_leak():
    hist = History(rss={"500:1.0": [(NOW - 1700, 0.1 * GB), (NOW - 600, 1.0 * GB)]})
    assert of(ev(facts(top=[proc(name="claude", rss=3 * GB)]), hist), "leak_suspect") == []


# --- idle containers (cumulative cgroup CPU counter) ----------------------
def c1(usec, mem=300_000_000):
    return ContainerInfo(id="c1", name="memobase-api", cpu_usec=usec, mem_bytes=mem)


def hist_cpu(*pts):
    return History(cpu={"c1": list(pts)})


IDLE = hist_cpu((NOW - H6 - 60, 1_000_000), (NOW - 3600, 1_500_000))  # 0.5 s CPU in 6 h ≈ 0.002%


def test_idle_container_is_info_with_stop_action():
    [c] = of(ev(facts(containers=[c1(1_600_000)]), IDLE), "container_idle")
    assert c.severity is Severity.info
    assert (c.actions[0].name, c.actions[0].target) == ("stop_container", "c1")


def test_idle_container_warns_when_ram_low():
    [c] = of(ev(facts(avail_pct=15, containers=[c1(1_600_000)]), IDLE), "container_idle")
    assert c.severity is Severity.warn


def test_busy_container_not_idle():
    busy_usec = 1_000_000 + int(0.05 * (H6 + 60) * 1e6)  # 5% of a CPU
    assert of(ev(facts(containers=[c1(busy_usec)]), IDLE), "container_idle") == []


def test_short_history_small_or_restarted_container_not_idle():
    assert of(ev(facts(containers=[c1(1_600_000)]), hist_cpu((NOW - 3600, 1_000_000))), "container_idle") == []
    assert of(ev(facts(containers=[c1(1_600_000, mem=50_000_000)]), IDLE), "container_idle") == []
    assert of(ev(facts(containers=[c1(10)]), IDLE), "container_idle") == []  # counter reset = restarted
