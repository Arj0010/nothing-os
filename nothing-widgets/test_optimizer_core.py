#!/usr/bin/env python3
"""Tests for the display-independent AGENT WORK optimizer core."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import optimizer_core as oc


def proc(pid, ppid, comm, exe, cgroup, uid=1000, start=None):
    return oc.ProcessInfo(
        identity=oc.ProcessIdentity(pid, start if start is not None else pid * 100),
        ppid=ppid,
        uid=uid,
        comm=comm,
        exe=exe,
        cgroup=cgroup,
    )


class FakeProcessSource:
    def __init__(self, start_times):
        self.start_times = start_times

    def start_time(self, pid):
        return self.start_times.get(pid)


def brave_groups():
    return (
        oc.AppGroup(
            key="brave",
            label="Brave",
            role=oc.Role.INTERACTIVE,
            processes=(
                proc(30, 1, "brave", "/usr/bin/brave-browser", "/user/app/brave"),
            ),
            unit="app-brave.scope",
        ),
    )


class PolicyTest(unittest.TestCase):
    def test_meeting_is_stronger_than_primary_and_interactive(self):
        self.assertGreater(oc.ROLE_STRENGTH[oc.Role.MEETING],
                           oc.ROLE_STRENGTH[oc.Role.PRIMARY])
        self.assertGreater(oc.ROLE_STRENGTH[oc.Role.PRIMARY],
                           oc.ROLE_STRENGTH[oc.Role.INTERACTIVE])

    def test_all_modes_define_every_role(self):
        for mode in oc.Mode:
            for role in oc.Role:
                policy = oc.policy_for(mode, role, active_primary=True)
                self.assertGreaterEqual(policy.cpu_weight, 1)
                self.assertLessEqual(policy.cpu_weight, 10_000)
                self.assertGreaterEqual(policy.io_weight, 1)
                self.assertLessEqual(policy.io_weight, 10_000)

    def test_auto_primary_relaxes_while_agents_are_idle(self):
        busy = oc.policy_for(oc.Mode.AUTO, oc.Role.PRIMARY, True)
        idle = oc.policy_for(oc.Mode.AUTO, oc.Role.PRIMARY, False)
        self.assertGreater(busy.cpu_weight, idle.cpu_weight)
        self.assertGreater(busy.io_weight, idle.io_weight)

    def test_no_policy_contains_an_automatic_termination_action(self):
        for mode in oc.Mode:
            for role in oc.Role:
                self.assertFalse(oc.policy_for(mode, role, True).terminate)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_settings_round_trip_only_the_manual_primary(self):
        path = os.path.join(self.tmp.name, "optimizer-settings.json")
        settings = oc.OptimizerSettings(manual_primary="org.gnome.Terminal")
        self.assertTrue(oc.save_settings(path, settings))
        self.assertEqual(oc.load_settings(path), settings)

    def test_corrupt_settings_start_with_agent_work_defaults(self):
        path = os.path.join(self.tmp.name, "optimizer-settings.json")
        with open(path, "w") as fh:
            fh.write("not json")
        self.assertEqual(oc.load_settings(path), oc.OptimizerSettings())
        self.assertEqual(oc.Mode.AUTO, oc.DEFAULT_LOGIN_MODE)

    def test_non_object_settings_start_with_agent_work_defaults(self):
        path = os.path.join(self.tmp.name, "optimizer-settings.json")
        with open(path, "w") as fh:
            fh.write("[]")
        self.assertEqual(oc.load_settings(path), oc.OptimizerSettings())


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.uid = 1000
        self.procs = (
            proc(10, 1, "claude", "/usr/bin/claude", "/user/app/term-a", self.uid),
            proc(11, 10, "python", "/usr/bin/python3", "/user/app/term-a", self.uid),
            proc(20, 1, "codex", "/usr/bin/codex", "/user/app/term-b", self.uid),
            proc(21, 20, "java", "/usr/bin/java", "/user/app/term-b", self.uid),
            proc(30, 1, "brave", "/usr/bin/brave-browser", "/user/app/brave", self.uid),
            proc(40, 1, "code", "/usr/bin/code", "/user/app/code", self.uid),
            proc(50, 1, "python", "/usr/bin/python3", "/user/app/unrelated", self.uid),
        )

    def test_all_agent_descendants_are_primary_regardless_of_language(self):
        groups = oc.classify_groups(self.procs, uid=self.uid)
        primary_pids = {
            process.identity.pid
            for group in groups if group.role == oc.Role.PRIMARY
            for process in group.processes
        }
        self.assertEqual(primary_pids, {10, 11, 20, 21})

    def test_each_agent_root_has_its_own_group(self):
        groups = oc.classify_groups(self.procs, uid=self.uid)
        agent_groups = [
            group for group in groups
            if group.role == oc.Role.PRIMARY
        ]
        self.assertEqual(
            [{process.identity.pid for process in group.processes} for group in agent_groups],
            [{10, 11}, {20, 21}],
        )

    def test_same_comm_outside_agent_tree_is_not_primary(self):
        rows = self.procs + (
            proc(51, 1, "claude", "/usr/bin/python3", "/user/app/unrelated-claude", self.uid),
        )
        groups = oc.classify_groups(rows, uid=self.uid)
        unrelated = next(
            group for group in groups
            if any(process.identity.pid == 51 for process in group.processes)
        )
        self.assertEqual(unrelated.role, oc.Role.BACKGROUND)

    def test_brave_and_code_have_interactive_roles(self):
        roles = {group.key: group.role for group in oc.classify_groups(self.procs, uid=self.uid)}
        self.assertEqual(roles["brave"], oc.Role.INTERACTIVE)
        self.assertEqual(roles["code"], oc.Role.INTERACTIVE)

    def test_manual_primary_override_is_stronger_than_default_interactive(self):
        groups = oc.classify_groups(self.procs, manual_primary="code", uid=self.uid)
        code = next(group for group in groups if group.key == "code")
        self.assertEqual(code.role, oc.Role.PRIMARY)

    def test_media_and_desktop_services_have_their_required_roles(self):
        rows = (
            proc(60, 1, "spotify", "/usr/bin/spotify", "/user/app/spotify", self.uid),
            proc(61, 1, "vlc", "/usr/bin/vlc", "/user/app/vlc", self.uid),
            proc(62, 1, "telegram", "/usr/bin/telegram-desktop", "/user/app/telegram", self.uid),
            proc(63, 1, "pipewire", "/usr/bin/pipewire", "/user/service/pipewire", self.uid),
            proc(64, 1, "guard", "/opt/nothing/optimizer_guard.py", "/user/app/guard", self.uid),
        )
        roles_by_pid = {
            process.identity.pid: group.role
            for group in oc.classify_groups(rows, uid=self.uid)
            for process in group.processes
        }
        self.assertEqual(roles_by_pid[60], oc.Role.MEDIA)
        self.assertEqual(roles_by_pid[61], oc.Role.MEDIA)
        self.assertEqual(roles_by_pid[62], oc.Role.MEDIA)
        self.assertEqual(roles_by_pid[63], oc.Role.PROTECTED)
        self.assertEqual(roles_by_pid[64], oc.Role.PROTECTED)

    def test_missing_cgroup_is_an_unmanaged_singleton(self):
        rows = (
            proc(70, 1, "python", "/usr/bin/python3", "", self.uid),
            proc(71, 1, "python", "/usr/bin/python3", "", self.uid),
        )
        groups = oc.classify_groups(rows, uid=self.uid)
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(not group.managed for group in groups))
        self.assertEqual([{process.identity.pid for process in group.processes} for group in groups], [{70}, {71}])

    def test_known_apps_without_cgroups_keep_their_safe_roles_unmanaged(self):
        rows = (
            proc(74, 1, "brave", "/usr/bin/brave-browser", "", self.uid),
            proc(75, 1, "spotify", "/usr/bin/spotify", "", self.uid),
        )
        groups = oc.classify_groups(rows, uid=self.uid)
        roles_by_pid = {
            process.identity.pid: group.role
            for group in groups
            for process in group.processes
        }
        self.assertTrue(all(not group.managed for group in groups))
        self.assertEqual(roles_by_pid[74], oc.Role.INTERACTIVE)
        self.assertEqual(roles_by_pid[75], oc.Role.MEDIA)

    def test_unresolved_identity_is_an_unmanaged_singleton(self):
        rows = (
            proc(72, 1, "python", "/usr/bin/python3", "/user/app/shared", self.uid, start=0),
            proc(73, 1, "python", "/usr/bin/python3", "/user/app/shared", self.uid),
        )
        groups = oc.classify_groups(rows, uid=self.uid)
        unresolved = next(group for group in groups if any(p.identity.pid == 72 for p in group.processes))
        self.assertFalse(unresolved.managed)
        self.assertEqual([process.identity.pid for process in unresolved.processes], [72])

    def test_same_process_rejects_reused_pid(self):
        source = FakeProcessSource({77: 9002})
        self.assertFalse(oc.same_process(oc.ProcessIdentity(77, 9001), source=source))

    def test_other_users_are_not_returned(self):
        rows = self.procs + (
            proc(90, 1, "claude", "/usr/bin/claude", "/other", 2000),
        )
        managed = [
            process for group in oc.classify_groups(rows, uid=self.uid)
            for process in group.processes
        ]
        self.assertNotIn(90, {process.identity.pid for process in managed})

    def test_read_processes_parses_final_stat_parenthesis_and_filters_uid(self):
        with tempfile.TemporaryDirectory() as proc_root:
            self._write_proc_fixture(proc_root, 101, "worker ) name", 1, 12345, self.uid,
                                     "/usr/bin/python3", "/user/app/python", 17)
            self._write_proc_fixture(proc_root, 102, "foreign", 1, 54321, 2000,
                                     "/usr/bin/python3", "/user/app/foreign", 20)

            processes = oc.read_processes(proc_root=proc_root, uid=self.uid)

        self.assertEqual(len(processes), 1)
        process = processes[0]
        self.assertEqual(process.identity, oc.ProcessIdentity(101, 12345))
        self.assertEqual(process.ppid, 1)
        self.assertEqual(process.uid, self.uid)
        self.assertEqual(process.exe, "/usr/bin/python3")
        self.assertEqual(process.cgroup, "/user/app/python")
        self.assertEqual(process.rss_bytes, 17 * os.sysconf("SC_PAGE_SIZE"))

    def _write_proc_fixture(self, root, pid, comm, ppid, start_time, uid, exe, cgroup, rss_pages):
        directory = os.path.join(root, str(pid))
        os.mkdir(directory)
        fields = ["R", str(ppid)] + ["0"] * 17 + [str(start_time)]
        with open(os.path.join(directory, "stat"), "w", encoding="utf-8") as fh:
            fh.write("%d (%s) %s\n" % (pid, comm, " ".join(fields)))
        with open(os.path.join(directory, "status"), "w", encoding="utf-8") as fh:
            fh.write("Name:\t%s\nUid:\t%d\t%d\t%d\t%d\n" % (comm, uid, uid, uid, uid))
        os.symlink(exe, os.path.join(directory, "exe"))
        with open(os.path.join(directory, "cgroup"), "w", encoding="utf-8") as fh:
            fh.write("0::%s\n" % cgroup)
        with open(os.path.join(directory, "statm"), "w", encoding="utf-8") as fh:
            fh.write("100 %d 0 0 0 0 0\n" % rss_pages)
        with open(os.path.join(directory, "comm"), "w", encoding="utf-8") as fh:
            fh.write("display-only\n")
