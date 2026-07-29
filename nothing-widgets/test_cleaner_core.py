#!/usr/bin/env python3
"""Tests for cleaner_core — the half of the CLEAN widget that deletes files.

Run:  python3 nothing-widgets/test_cleaner_core.py
Stdlib only, no display needed.
"""
import json, os, shutil, sys, tempfile, time, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cleaner_core as cc


class GuardTest(unittest.TestCase):
    """The path guard is the only thing standing between a bug and your home dir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, "cache")
        self.outside = os.path.join(self.tmp, "projects")
        os.makedirs(self.cache); os.makedirs(self.outside)
        self._saved = cc.ALLOWED_ROOTS
        cc.ALLOWED_ROOTS = (self.cache,)

    def tearDown(self):
        cc.ALLOWED_ROOTS = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_allows_path_inside_root(self):
        p = os.path.join(self.cache, "brave", "x")
        self.assertEqual(cc.guard(p), os.path.realpath(p))

    def test_allows_the_root_itself(self):
        self.assertEqual(cc.guard(self.cache), os.path.realpath(self.cache))

    def test_rejects_path_outside_root(self):
        with self.assertRaises(cc.UnsafePath):
            cc.guard(os.path.join(self.outside, "important"))

    def test_rejects_traversal_out_of_root(self):
        with self.assertRaises(cc.UnsafePath):
            cc.guard(os.path.join(self.cache, "..", "projects", "important"))

    def test_rejects_sibling_with_shared_prefix(self):
        # ".../cache-backup" must not pass just because it starts with ".../cache"
        with self.assertRaises(cc.UnsafePath):
            cc.guard(self.cache + "-backup")

    def test_rejects_symlink_pointing_outside_root(self):
        link = os.path.join(self.cache, "escape")
        os.symlink(self.outside, link)
        with self.assertRaises(cc.UnsafePath):
            cc.guard(link)


class EmptyDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, "cache")
        self.outside = os.path.join(self.tmp, "projects")
        os.makedirs(self.cache); os.makedirs(self.outside)
        self._saved = cc.ALLOWED_ROOTS
        cc.ALLOWED_ROOTS = (self.cache,)

    def tearDown(self):
        cc.ALLOWED_ROOTS = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, path, size):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"x" * size)

    def test_removes_contents_but_keeps_the_directory(self):
        d = os.path.join(self.cache, "brave")
        self._write(os.path.join(d, "a.bin"), 1000)
        self._write(os.path.join(d, "sub", "b.bin"), 500)
        freed = cc._empty_dir(d)
        self.assertEqual(freed, 1500)
        self.assertTrue(os.path.isdir(d), "cache dir itself must survive")
        self.assertEqual(os.listdir(d), [])

    def test_refuses_to_empty_a_directory_outside_the_root(self):
        self._write(os.path.join(self.outside, "keep.txt"), 10)
        with self.assertRaises(cc.UnsafePath):
            cc._empty_dir(self.outside)
        self.assertTrue(os.path.exists(os.path.join(self.outside, "keep.txt")))

    def test_symlink_out_is_unlinked_not_followed(self):
        d = os.path.join(self.cache, "brave")
        os.makedirs(d)
        self._write(os.path.join(self.outside, "precious.txt"), 10)
        os.symlink(self.outside, os.path.join(d, "escape"))
        cc._empty_dir(d)
        self.assertTrue(os.path.exists(os.path.join(self.outside, "precious.txt")),
                        "must not delete through a symlink")

    def test_missing_directory_is_a_no_op(self):
        self.assertEqual(cc._empty_dir(os.path.join(self.cache, "nope")), 0)


class DuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sums_nested_files(self):
        os.makedirs(os.path.join(self.tmp, "a", "b"))
        with open(os.path.join(self.tmp, "a", "f1"), "wb") as fh: fh.write(b"x" * 100)
        with open(os.path.join(self.tmp, "a", "b", "f2"), "wb") as fh: fh.write(b"x" * 250)
        self.assertEqual(cc.du_bytes(self.tmp), 350)

    def test_missing_path_is_zero(self):
        self.assertEqual(cc.du_bytes(os.path.join(self.tmp, "gone")), 0)


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hist = cc.History(os.path.join(self.tmp, "h.jsonl"), days=7)
        self.now = 1_800_000_000.0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _append(self, when, rows):
        self.hist.append([{"name": n, "rss": r} for n, r in rows], now=when)

    def test_load_drops_entries_older_than_the_window(self):
        self._append(self.now - 8 * 86400, [("old", 1)])
        self._append(self.now - 1 * 86400, [("new", 2)])
        entries = self.hist.load(now=self.now)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["p"][0][0], "new")

    def test_prune_rewrites_the_file(self):
        self._append(self.now - 8 * 86400, [("old", 1)])
        self._append(self.now - 1 * 86400, [("new", 2)])
        self.hist.prune(now=self.now)
        with open(self.hist.path) as fh:
            lines = [ln for ln in fh if ln.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["p"][0][0], "new")

    def test_corrupt_line_is_skipped_not_fatal(self):
        self._append(self.now - 3600, [("good", 5)])
        with open(self.hist.path, "a") as fh:
            fh.write("{not json\n")
        self.assertEqual(len(self.hist.load(now=self.now)), 1)

    def test_series_reports_peak_per_app(self):
        self._append(self.now - 3 * 86400, [("brave", 100), ("code", 900)])
        self._append(self.now - 2 * 86400, [("brave", 700), ("code", 400)])
        rows = self.hist.series(buckets=12, top_n=2, now=self.now)
        peaks = {r["name"]: r["peak"] for r in rows}
        self.assertEqual(peaks["brave"], 700)
        self.assertEqual(peaks["code"], 900)

    def test_series_sorted_by_peak_descending(self):
        self._append(self.now - 3600, [("small", 10), ("big", 5000)])
        rows = self.hist.series(buckets=12, top_n=2, now=self.now)
        self.assertEqual(rows[0]["name"], "big")

    def test_series_buckets_have_fixed_width(self):
        self._append(self.now - 3600, [("a", 10)])
        rows = self.hist.series(buckets=12, top_n=1, now=self.now)
        self.assertEqual(len(rows[0]["points"]), 12)

    def test_series_on_empty_history(self):
        self.assertEqual(self.hist.series(now=self.now), [])


class ProcTest(unittest.TestCase):
    def test_sample_returns_plausible_rows(self):
        rows = cc.sample(top_n=5)
        self.assertTrue(rows, "should see at least one process")
        self.assertLessEqual(len(rows), 5)
        for r in rows:
            self.assertGreater(r["rss"], 0)
            self.assertIn("name", r)
            self.assertIsInstance(r["pid"], int)
        self.assertEqual(rows, sorted(rows, key=lambda r: -r["rss"]))

    def test_sample_excludes_this_process(self):
        pids = {r["pid"] for r in cc.sample(top_n=64)}
        self.assertNotIn(os.getpid(), pids)

    def test_kill_refuses_own_pid(self):
        self.assertFalse(cc.kill(os.getpid()))


class AliveTest(unittest.TestCase):
    """Regression: _alive() once substring-matched command lines, so a shell that
    merely mentioned 'conky' counted as conky running — and revive() then left the
    real process dead."""

    def test_matches_a_real_running_process_by_name(self):
        # this interpreter is running; find what the kernel calls it
        with open("/proc/%d/comm" % os.getpid()) as fh:
            me = fh.read().strip()
        # our own pid is excluded, so this only passes if _alive found a sibling;
        # spawn one to be sure there is a second process of the same name
        p = __import__("subprocess").Popen([sys.executable, "-c", "import time;time.sleep(3)"])
        try:
            time.sleep(0.4)
            self.assertTrue(cc._alive(me))
        finally:
            p.kill(); p.wait()

    def test_ignores_a_process_that_only_mentions_the_name(self):
        needle = "definitely-not-a-real-process-name"
        p = __import__("subprocess").Popen(
            [sys.executable, "-c", "import time;time.sleep(3)  # %s" % needle])
        try:
            time.sleep(0.4)
            self.assertFalse(cc._alive(needle),
                             "a command line mentioning the name is not the process")
        finally:
            p.kill(); p.wait()

    def test_excludes_own_pid(self):
        with open("/proc/%d/comm" % os.getpid()) as fh:
            me = fh.read().strip()
        # with no sibling of the same name, only our own process could match
        found = any(comm == me and pid != os.getpid()
                    for pid, comm, _c, _r in cc._iter_procs())
        if not found:
            self.assertFalse(cc._alive(me))

    def test_snapshot_covers_every_watched_process(self):
        snap = cc.snapshot_watched()
        self.assertEqual(set(snap), set(cc.WATCHED))
        for v in snap.values():
            self.assertIsInstance(v, bool)

    def test_revive_skips_anything_that_was_already_down(self):
        # nothing was up -> nothing to bring back, and no commands are run
        self.assertEqual(cc.revive({n: False for n in cc.WATCHED}), [])

    def test_revive_relaunches_a_process_that_died(self):
        """Regression: revive() used to wait on the command it spawned. A daemon
        never exits, so it timed out and reported failure while the process was
        in fact running fine."""
        tmp = tempfile.mkdtemp()
        try:
            # a uniquely-named symlink to sleep, so comm cannot collide with
            # anything else on the machine (comm is capped at 15 chars)
            name = "nothingprobe"
            binpath = os.path.join(tmp, name)
            os.symlink("/usr/bin/sleep", binpath)
            saved = cc.WATCHED
            cc.WATCHED = {"probe": dict(comm=name, revive=[binpath, "10"])}
            try:
                self.assertFalse(cc._alive(name), "probe must not be running yet")
                back = cc.revive({"probe": True}, wait=5.0)
                self.assertEqual(back, ["probe"])
                self.assertTrue(cc._alive(name))
            finally:
                cc.WATCHED = saved
                __import__("subprocess").run(["pkill", "-x", name])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TargetsTest(unittest.TestCase):
    def test_keys_are_unique(self):
        keys = [t.key for t in cc.TARGETS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_held_targets_are_excluded_from_auto_clean(self):
        rows = [{"key": "brave", "held": False, "state": "ok", "bytes": 10},
                {"key": "puppeteer", "held": True, "state": "ok", "bytes": 999},
                {"key": "apt", "held": False, "state": "auth", "bytes": 0},
                {"key": "pip", "held": False, "state": "ok", "bytes": 0}]
        self.assertEqual(cc.auto_keys(rows), ["brave"])

    def test_reclaimable_ignores_held_and_unauthorised(self):
        rows = [{"key": "brave", "held": False, "state": "ok", "bytes": 10},
                {"key": "puppeteer", "held": True, "state": "ok", "bytes": 999},
                {"key": "apt", "held": False, "state": "auth", "bytes": 500}]
        self.assertEqual(cc.reclaimable(rows), 10)

    def test_every_non_command_target_lives_under_an_allowed_root(self):
        for t in cc.TARGETS:
            if t.cmd:
                continue
            for p in t.paths:
                cc.guard(p)   # raises if a target was added outside the safe roots

    def test_clean_unknown_key_is_an_error_not_a_crash(self):
        self.assertEqual(cc.clean_one("nope")["state"], "err")

    def test_root_rows_report_auth_when_the_rule_is_absent(self):
        """Regression: sudo_ok() used to ask `sudo -n -l`, which says yes for a user
        with a blanket ALL=(ALL) ALL entry and warm credentials. The card then
        advertised ~2 GB it could not actually free."""
        saved = cc.SUDOERS_RULE
        cc.SUDOERS_RULE = "/nonexistent/nothing-cleaner"
        try:
            self.assertFalse(cc.sudo_ok())
            rows = {r["key"]: r for r in cc.scan()}
            self.assertEqual(rows["apt"]["state"], "auth")
            self.assertEqual(rows["journal"]["state"], "auth")
            self.assertNotIn("apt", cc.auto_keys(list(rows.values())))
            # The row still carries its size, so the card can say "1.5G, one install
            # step away" — but that size must not reach the advertised total.
            self.assertGreater(rows["apt"]["bytes"], 0)
            total = cc.reclaimable(list(rows.values()))
            self.assertLess(total, rows["apt"]["bytes"])
            self.assertEqual(total, sum(r["bytes"] for r in rows.values()
                                        if r["state"] == "ok" and not r["held"]))
        finally:
            cc.SUDOERS_RULE = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
