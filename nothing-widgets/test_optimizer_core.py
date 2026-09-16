#!/usr/bin/env python3
"""Tests for the display-independent AGENT WORK optimizer core."""
import inspect
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import optimizer_core as oc
import optimizer_guard as og


def proc(pid, ppid, comm, exe, cgroup, uid=1000, start=None, cmdline=()):
    fields = dict(
        identity=oc.ProcessIdentity(pid, start if start is not None else pid * 100),
        ppid=ppid,
        uid=uid,
        comm=comm,
        exe=exe,
        cgroup=cgroup,
    )
    if cmdline:
        fields["cmdline"] = cmdline
    return oc.ProcessInfo(**fields)


class FakeProcessSource:
    def __init__(self, start_times):
        self.start_times = start_times

    def start_time(self, pid):
        return self.start_times.get(pid)


class CurrentFakeProcessSource(FakeProcessSource):
    """A controlled source whose generated test identities are current by default."""

    def __init__(self, start_times, states=None):
        super().__init__(start_times)
        self.states = states or {}

    def start_time(self, pid):
        return self.start_times.get(pid, pid * 100)

    def state(self, pid):
        return self.states.get(pid, "R")


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, delay):
        self.value += delay


class FakeLease:
    """A fully in-memory guard protocol with controllable registration failure."""

    def __init__(self, fail_track=False, fail_untrack=False):
        self.fail_track = fail_track
        self.fail_untrack = fail_untrack
        self.tracked = []
        self.untracked = []

    def track(self, identity, backend="process", unit=""):
        if self.fail_track:
            return False
        self.tracked.append((identity, backend, unit))
        return True

    def untrack(self, identity):
        self.untracked.append(identity)
        return not self.fail_untrack


class FailingJournal:
    def load(self):
        return oc._empty_runtime_journal(), oc.Capability("journal", "full", "test")

    def replace_action(self, action, entries):
        del action, entries
        return False


class FailingReplaceJournal(oc.MemoryJournal):
    def replace_entries(self, entries):
        del entries
        return False


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


class FakeRunner:
    """Controlled command runner for metadata and capability probes."""

    def __init__(self, responses=None, missing=(), fail=(), outputs=None, fail_prefixes=()):
        self.responses = responses or outputs or {}
        self.missing = set(missing)
        self.fail = set(fail)
        self.fail_prefixes = set(fail_prefixes)
        self.commands = []

    def __call__(self, command):
        return self.run(command)

    def run(self, command, input_text=""):
        command = tuple(command)
        self.commands.append(command)
        executable = command[0]
        if (executable in self.missing or executable in self.fail
                or any(command[:len(prefix)] == prefix for prefix in self.fail_prefixes)):
            return False, ""
        value = self.responses.get(command)
        if isinstance(value, list):
            value = value.pop(0) if value else ""
        if value is None and command[:3] == ("systemctl", "--user", "show") \
                and command[-1:] == ("--property=FreezerState",):
            value = "FreezerState=running"
        if value is None:
            value = ""
        return value if isinstance(value, tuple) else (True, value)


def sample_groups():
    return (
        oc.AppGroup("agent", "Claude", oc.Role.PRIMARY,
                    (proc(10, 1, "claude", "/usr/bin/claude", "/app-agent.scope"),),
                    unit="app-agent.scope"),
        oc.AppGroup("postman", "Postman", oc.Role.BACKGROUND,
                    (proc(20, 1, "postman", "/usr/bin/postman", "/app-postman.scope"),),
                    unit="app-postman.scope"),
    )


def resource_runner(**kwargs):
    outputs = {("powerprofilesctl", "get"): "balanced"}
    for group in sample_groups():
        outputs[("systemctl", "--user", "show", group.unit,
                 "--property=CPUWeight,IOWeight,MemoryLow")] = (
            "CPUWeight=100\nIOWeight=100\nMemoryLow=0\n")
        pid = str(group.processes[0].identity.pid)
        outputs[("ps", "-o", "ni=", "-p", pid)] = "0"
        outputs[("ionice", "-p", pid)] = "none: prio 0"
    outputs.update(kwargs.pop("outputs", {}))
    return FakeRunner(outputs=outputs, **kwargs)


class ResourceControllerTest(unittest.TestCase):
    def controller(self, runner, source=None):
        return oc.ResourceController(runner, uid=1000,
                                     source=source or FakeProcessSource({10: 1000, 20: 2000}),
                                     journal=oc.MemoryJournal())

    def test_performance_requests_platform_performance_profile(self):
        runner = resource_runner()
        self.controller(runner).apply(oc.Mode.PERFORMANCE, sample_groups(), True)
        self.assertIn(("powerprofilesctl", "set", "performance"), runner.commands)

    def test_modes_restore_original_profile_and_capture_it_once(self):
        runner = resource_runner()
        controller = self.controller(runner)
        for mode, wanted in ((oc.Mode.BATTERY, "power-saver"),
                             (oc.Mode.FOCUS, "balanced"), (oc.Mode.AUTO, "balanced")):
            controller.apply(mode, (), True)
            self.assertIn(("powerprofilesctl", "set", wanted), runner.commands)
        controller.restore()
        self.assertEqual(runner.commands.count(("powerprofilesctl", "get")), 2)
        self.assertEqual(runner.commands[-1], ("powerprofilesctl", "set", "balanced"))

    def test_unit_policy_uses_weights_and_only_soft_memory_protection(self):
        runner = resource_runner()
        state = self.controller(runner).apply(oc.Mode.FOCUS, sample_groups(), True)
        self.assertIn(("systemctl", "--user", "set-property", "--runtime", "app-agent.scope",
                       "CPUWeight=1400", "IOWeight=1200", "MemoryLow=256M"), runner.commands)
        self.assertIn(("systemctl", "--user", "set-property", "--runtime", "app-postman.scope",
                       "CPUWeight=20", "IOWeight=20", "MemoryLow=0"), runner.commands)
        self.assertEqual([g.backend for g in state.groups], ["systemd", "systemd"])
        self.assertEqual({c.name: c.level for c in state.capabilities}["cpu_io"], "full")

    def test_background_fallback_is_only_deprioritized(self):
        runner = resource_runner(fail_prefixes={("systemctl", "--user", "set-property")})
        state = self.controller(runner).apply(oc.Mode.FOCUS, sample_groups(), True)
        self.assertIn(("renice", "-n", "12", "-p", "20"), runner.commands)
        self.assertIn(("ionice", "-c", "2", "-n", "7", "-p", "20"), runner.commands)
        self.assertEqual({c.name: c.level for c in state.capabilities}["cpu_io"], "fallback")
        self.assertTrue(all("SIGTERM" not in c and "SIGKILL" not in c for c in runner.commands))

    def test_partial_backend_failure_keeps_power_and_io_success(self):
        runner = resource_runner(fail={"renice"}, fail_prefixes={("systemctl", "--user", "set-property")})
        state = self.controller(runner).apply(oc.Mode.PERFORMANCE, sample_groups(), True)
        levels = {c.name: c.level for c in state.capabilities}
        self.assertEqual(levels["power"], "full")
        self.assertEqual(levels["cpu_io"], "fallback")
        self.assertEqual(levels["memory"], "unavailable")
        self.assertTrue(any(g.backend == "process" for g in state.groups))

    def test_pid_reuse_between_renice_and_ionice_skips_second_mutation(self):
        source = FakeProcessSource({10: 1000, 20: 2000})
        runner = resource_runner(fail_prefixes={("systemctl", "--user", "set-property")})
        original_run = runner.run
        def run(command, input_text=""):
            result = original_run(command, input_text)
            if command[:2] == ("renice", "-n"):
                source.start_times[int(command[-1])] += 1
            return result
        runner.run = run
        self.controller(runner, source).apply(oc.Mode.FOCUS, sample_groups(), True)
        self.assertFalse(any(c[:2] == ("ionice", "-c") for c in runner.commands))

    def test_stale_foreign_and_unmanaged_targets_are_skipped(self):
        groups = sample_groups()
        variants = (
            replace(groups[0], managed=False),
            replace(groups[0], processes=(replace(groups[0].processes[0], uid=2000),)),
            groups[1],
        )
        runner = resource_runner()
        state = self.controller(runner, FakeProcessSource({20: 2001})).apply(
            oc.Mode.FOCUS, variants, True)
        self.assertFalse(state.groups)
        self.assertFalse(any(c[0] in ("renice", "ionice", "systemctl") for c in runner.commands))

    def test_unreadable_original_values_are_not_mutated(self):
        runner = FakeRunner()
        state = self.controller(runner).apply(oc.Mode.FOCUS, sample_groups(), True)
        self.assertFalse(state.groups)
        self.assertFalse(any(c[:2] in (("renice", "-n"), ("ionice", "-c"),
                                      ("powerprofilesctl", "set")) for c in runner.commands))

    def test_restore_only_successes_and_retry_failed_restore(self):
        runner = resource_runner(fail={"renice"}, fail_prefixes={("systemctl", "--user", "set-property")})
        controller = self.controller(runner)
        controller.apply(oc.Mode.FOCUS, sample_groups()[1:], True)
        runner.responses[("ionice", "-p", "20")] = "best-effort: prio 7"
        runner.fail.add("ionice")
        controller.restore()
        runner.fail.clear()
        controller.restore()
        self.assertIn(("ionice", "-c", "0", "-p", "20"), runner.commands)
        self.assertFalse(any(c[:2] == ("renice", "-n") and c[2] == "0" for c in runner.commands))
        count = len(runner.commands)
        controller.restore()
        self.assertEqual(len(runner.commands), count)

    def test_restore_does_not_overwrite_external_priority_change(self):
        runner = resource_runner(fail_prefixes={("systemctl", "--user", "set-property")})
        controller = self.controller(runner)
        controller.apply(oc.Mode.FOCUS, sample_groups()[1:], True)
        runner.responses[("ps", "-o", "ni=", "-p", "20")] = "15"
        runner.responses[("ionice", "-p", "20")] = "idle"
        before = len(runner.commands)
        controller.restore()
        self.assertFalse(any(c[:2] in (("renice", "-n"), ("ionice", "-c"))
                             for c in runner.commands[before:]))

    def test_basic_network_is_nonmutating_and_injectable(self):
        backend = oc.BasicNetworkBackend()
        expected = oc.Capability("network", "fallback", "NETWORK · BASIC")
        self.assertEqual(backend.apply(sample_groups(), {}), expected)
        self.assertEqual(backend.restore(), expected)
        runner = resource_runner()
        state = oc.ResourceController(
            runner, uid=1000, network=backend, journal=oc.MemoryJournal()).apply(
                oc.Mode.AUTO, (), True)
        self.assertIn(expected, state.capabilities)

    def test_restore_rechecks_pid_identity(self):
        source = FakeProcessSource({20: 2000})
        runner = resource_runner(fail_prefixes={("systemctl", "--user", "set-property")})
        controller = self.controller(runner, source)
        controller.apply(oc.Mode.FOCUS, sample_groups()[1:], True)
        source.start_times[20] = 2001
        before = len(runner.commands)
        controller.restore()
        self.assertFalse(any(c[0] in ("renice", "ionice", "ps") for c in runner.commands[before:]))

    def test_memory_success_does_not_claim_cpu_io_success(self):
        runner = resource_runner(fail={"renice", "ionice"})
        run = runner.run
        def partial(command, input_text=""):
            result = run(command, input_text)
            if command[:3] == ("systemctl", "--user", "set-property"):
                runner.responses[("systemctl", "--user", "show", "app-agent.scope",
                                  "--property=CPUWeight,IOWeight,MemoryLow")] = (
                    "CPUWeight=100\nIOWeight=100\nMemoryLow=268435456\n")
                return False, "CPU and I/O weights unavailable"
            return result
        runner.run = partial
        state = self.controller(runner).apply(oc.Mode.FOCUS, sample_groups()[:1], True)
        levels = {c.name: c.level for c in state.capabilities}
        self.assertEqual(levels["memory"], "full")
        self.assertEqual(levels["cpu_io"], "unavailable")

    def test_partial_unit_write_restores_only_changed_properties(self):
        runner = resource_runner(fail={"renice", "ionice"})
        run = runner.run
        def partial(command, input_text=""):
            result = run(command, input_text)
            if command[:3] == ("systemctl", "--user", "set-property") and len(command) == 8:
                runner.responses[("systemctl", "--user", "show", "app-agent.scope",
                                  "--property=CPUWeight,IOWeight,MemoryLow")] = (
                    "CPUWeight=1400\nIOWeight=100\nMemoryLow=268435456\n")
                return False, "I/O weight unavailable"
            return result
        runner.run = partial
        controller = self.controller(runner)
        controller.apply(oc.Mode.FOCUS, sample_groups()[:1], True)
        before = len(runner.commands)
        controller.restore()
        restored = [c[-1] for c in runner.commands[before:]
                    if c[:3] == ("systemctl", "--user", "set-property")]
        self.assertCountEqual(restored, ["CPUWeight=100", "MemoryLow=0"])
        before = len(runner.commands)
        controller.restore()
        self.assertEqual(len(runner.commands), before)

    def test_unit_restore_keeps_activation_original_across_mode_changes(self):
        runner = resource_runner()
        controller = self.controller(runner)
        controller.apply(oc.Mode.FOCUS, sample_groups()[:1], True)
        query = ("systemctl", "--user", "show", "app-agent.scope",
                 "--property=CPUWeight,IOWeight,MemoryLow")
        runner.responses[query] = "CPUWeight=1400\nIOWeight=1200\nMemoryLow=268435456\n"
        controller.apply(oc.Mode.PERFORMANCE, sample_groups()[:1], True)
        runner.responses[query] = "CPUWeight=2000\nIOWeight=1800\nMemoryLow=268435456\n"
        runner.responses[("powerprofilesctl", "get")] = "performance"
        before = len(runner.commands)
        controller.restore()
        restored = [c[-1] for c in runner.commands[before:]
                    if c[:3] == ("systemctl", "--user", "set-property")]
        self.assertCountEqual(restored, ["CPUWeight=100", "IOWeight=100", "MemoryLow=0"])
        self.assertEqual(runner.commands[-1], ("powerprofilesctl", "set", "balanced"))

    def test_failed_power_apply_is_not_restored_but_units_still_apply(self):
        runner = resource_runner(fail_prefixes={("powerprofilesctl", "set")})
        controller = self.controller(runner)
        state = controller.apply(oc.Mode.BATTERY, sample_groups(), True)
        self.assertEqual({c.name: c.level for c in state.capabilities}["power"], "unavailable")
        self.assertEqual(len(state.groups), 2)
        before = len(runner.commands)
        controller.restore()
        self.assertFalse(any(c[0] == "powerprofilesctl" for c in runner.commands[before:]))

    def test_shared_unit_uses_independent_process_fallback(self):
        groups = sample_groups()
        runner = resource_runner()
        state = self.controller(runner).apply(oc.Mode.FOCUS, (
            groups[0], replace(groups[1], unit=groups[0].unit)), True)
        self.assertFalse(any(c[0] == "systemctl" for c in runner.commands))
        self.assertEqual([g.backend for g in state.groups], ["process", "process"])

    def test_systemd_pid_reuse_during_original_probe_prevents_write(self):
        source = FakeProcessSource({10: 1000})
        runner = resource_runner()
        run = runner.run
        def reuse(command, input_text=""):
            result = run(command, input_text)
            if command[:3] == ("systemctl", "--user", "show"):
                source.start_times[10] = 1001
            return result
        runner.run = reuse
        self.controller(runner, source).apply(oc.Mode.FOCUS, sample_groups()[:1], True)
        self.assertFalse(any(c[:3] == ("systemctl", "--user", "set-property")
                             or c[:2] in (("renice", "-n"), ("ionice", "-c")) for c in runner.commands))

    def test_malformed_power_restore_probe_keeps_restore_pending(self):
        runner = resource_runner()
        controller = self.controller(runner)
        controller.apply(oc.Mode.BATTERY, (), True)
        runner.responses[("powerprofilesctl", "get")] = ""
        caps = {c.name: c.level for c in controller.restore()}
        self.assertEqual(caps["power"], "unavailable")
        runner.responses[("powerprofilesctl", "get")] = "power-saver"
        controller.restore()
        self.assertEqual(runner.commands[-1], ("powerprofilesctl", "set", "balanced"))

    def test_network_exceptions_do_not_interrupt_power_or_restore(self):
        class FailedNetwork:
            def apply(self, groups, policies):
                raise OSError("helper unavailable")
            def restore(self):
                raise OSError("helper unavailable")
        runner = resource_runner()
        controller = oc.ResourceController(
            runner, uid=1000, network=FailedNetwork(), journal=oc.MemoryJournal())
        state = controller.apply(oc.Mode.BATTERY, (), True)
        levels = {c.name: c.level for c in state.capabilities}
        self.assertEqual(levels["power"], "full")
        self.assertEqual(levels["network"], "unavailable")
        runner.responses[("powerprofilesctl", "get")] = "power-saver"
        controller.restore()
        self.assertEqual(runner.commands[-1], ("powerprofilesctl", "set", "balanced"))

    def test_unit_replacement_captures_its_own_baseline(self):
        runner = resource_runner()
        source = FakeProcessSource({10: 1000})
        controller = self.controller(runner, source)
        group = sample_groups()[0]
        controller.apply(oc.Mode.FOCUS, (group,), True)
        query = ("systemctl", "--user", "show", group.unit,
                 "--property=CPUWeight,IOWeight,MemoryLow")
        source.start_times[10] = 1001
        replacement = replace(group, processes=(replace(
            group.processes[0], identity=oc.ProcessIdentity(10, 1001)),))
        runner.responses[query] = "CPUWeight=222\nIOWeight=333\nMemoryLow=444\n"
        controller.apply(oc.Mode.FOCUS, (replacement,), True)
        runner.responses[query] = "CPUWeight=1400\nIOWeight=1200\nMemoryLow=268435456\n"
        before = len(runner.commands)
        controller.restore()
        restored = [c[-1] for c in runner.commands[before:]
                    if c[:3] == ("systemctl", "--user", "set-property")]
        self.assertCountEqual(restored, ["CPUWeight=222", "IOWeight=333", "MemoryLow=444"])

    def test_failed_unit_readback_remains_pending_until_partial_write_is_restored(self):
        runner = resource_runner(fail={"renice", "ionice"})
        query = ("systemctl", "--user", "show", "app-agent.scope",
                 "--property=CPUWeight,IOWeight,MemoryLow")
        run = runner.run
        def partial(command, input_text=""):
            result = run(command, input_text)
            if command[:3] == ("systemctl", "--user", "set-property") and len(command) == 8:
                runner.responses[query] = (False, "temporarily unavailable")
                return False, "partial write"
            return result
        runner.run = partial
        controller = self.controller(runner)
        controller.apply(oc.Mode.FOCUS, sample_groups()[:1], True)
        for _ in range(2):
            caps = {c.name: c.level for c in controller.restore()}
            self.assertEqual(caps["cpu_io"], "unavailable")
            self.assertEqual(caps["memory"], "unavailable")
        runner.responses[query] = "CPUWeight=1400\nIOWeight=100\nMemoryLow=0\n"
        before = len(runner.commands)
        caps = {c.name: c.level for c in controller.restore()}
        restored = [c[-1] for c in runner.commands[before:]
                    if c[:3] == ("systemctl", "--user", "set-property")]
        self.assertEqual(restored, ["CPUWeight=100"])
        self.assertEqual(caps["cpu_io"], "full")
        before = len(runner.commands)
        controller.restore()
        self.assertEqual(len(runner.commands), before)

    def test_process_reapply_restores_external_nice_and_io_baselines(self):
        runner = resource_runner(fail_prefixes={("systemctl", "--user", "set-property")})
        controller = self.controller(runner)
        controller.apply(oc.Mode.FOCUS, sample_groups()[1:], True)
        runner.responses[("ps", "-o", "ni=", "-p", "20")] = "15"
        runner.responses[("ionice", "-p", "20")] = "best-effort: prio 5"
        controller.apply(oc.Mode.PERFORMANCE, sample_groups()[1:], True)
        runner.responses[("ps", "-o", "ni=", "-p", "20")] = "8"
        runner.responses[("ionice", "-p", "20")] = "best-effort: prio 7"
        before = len(runner.commands)
        controller.restore()
        self.assertIn(("renice", "-n", "15", "-p", "20"), runner.commands[before:])
        self.assertIn(("ionice", "-c", "2", "-n", "5", "-p", "20"), runner.commands[before:])

    def test_unit_reapply_restores_external_property_baselines(self):
        runner = resource_runner()
        controller = self.controller(runner)
        controller.apply(oc.Mode.FOCUS, sample_groups()[:1], True)
        query = ("systemctl", "--user", "show", "app-agent.scope",
                 "--property=CPUWeight,IOWeight,MemoryLow")
        runner.responses[query] = "CPUWeight=222\nIOWeight=333\nMemoryLow=444\n"
        controller.apply(oc.Mode.PERFORMANCE, sample_groups()[:1], True)
        runner.responses[query] = "CPUWeight=2000\nIOWeight=1800\nMemoryLow=268435456\n"
        before = len(runner.commands)
        controller.restore()
        restored = [c[-1] for c in runner.commands[before:]
                    if c[:3] == ("systemctl", "--user", "set-property")]
        self.assertCountEqual(restored, ["CPUWeight=222", "IOWeight=333", "MemoryLow=444"])

    def test_resource_controller_journals_only_confirmed_process_ownership(self):
        runner = resource_runner(fail_prefixes={("systemctl", "--user", "set-property")})
        source = FakeProcessSource({20: 2000})
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "optimizer-runtime.json")
            controller = oc.ResourceController(
                runner, uid=1000, source=source, journal=oc.RuntimeJournal(path))
            controller.apply(oc.Mode.FOCUS, sample_groups()[1:], True)
            journal, capability = oc.load_runtime_journal(path)
            self.assertEqual(capability.level, "full")
            self.assertEqual(len(journal["entries"]), 1)
            entry = journal["entries"][0]
            self.assertEqual(
                set(entry),
                {"action", "group_key", "identities", "unit", "original", "owned"},
            )
            self.assertEqual(entry["action"], "resource")
            self.assertEqual(entry["group_key"], "postman")
            self.assertEqual(entry["identities"], [{"pid": 20, "start_time": 2000}])
            self.assertEqual(entry["original"], {"cpu": 0, "io": [0, 0]})
            self.assertEqual(entry["owned"], {"cpu": 12, "io": [2, 7]})
            controller.restore()
            journal, _capability = oc.load_runtime_journal(path)
            self.assertEqual(journal["entries"], [])


class CaptureTest(unittest.TestCase):
    def test_attributed_brave_capture_promotes_brave(self):
        state = oc.CaptureState(active=True, brave=True, certain=True)
        groups = oc.promote_capture(brave_groups(), state)
        self.assertEqual(groups[0].role, oc.Role.MEETING)

    def test_uncertain_capture_fails_safe_when_brave_is_running(self):
        state = oc.CaptureState(active=True, brave=False, certain=False)
        groups = oc.promote_capture(brave_groups(), state)
        self.assertEqual(groups[0].role, oc.Role.MEETING)

    def test_probe_failure_never_demotes_brave_below_interactive(self):
        state = oc.CaptureState(active=False, brave=False, certain=False)
        groups = oc.promote_capture(brave_groups(), state)
        self.assertEqual(groups[0].role, oc.Role.INTERACTIVE)

    def test_pactl_metadata_attributes_brave_capture(self):
        runner = FakeRunner({
            ("pactl", "-f", "json", "list", "source-outputs"): """
                [{"properties": {"application.process.binary": "brave-browser"}}]
            """,
        })
        self.assertEqual(
            oc.capture_state(runner),
            oc.CaptureState(active=True, brave=True, certain=True),
        )

    def test_pactl_failure_falls_back_to_pipewire_metadata(self):
        runner = FakeRunner(
            {
                ("pw-dump",): """
                    [{"info": {"props": {
                        "media.class": "Stream/Input/Audio",
                        "application.name": "Brave"
                    }}}]
                """,
            },
            fail={"pactl"},
        )
        self.assertEqual(
            oc.capture_state(runner),
            oc.CaptureState(active=True, brave=True, certain=True),
        )

    def test_pactl_capture_without_identity_is_uncertain_and_protects_brave(self):
        runner = FakeRunner({
            ("pactl", "-f", "json", "list", "source-outputs"): """
                [{"properties": {}}]
            """,
        })
        state = oc.capture_state(runner)
        self.assertEqual(
            state,
            oc.CaptureState(active=True, brave=False, certain=False),
        )
        self.assertEqual(oc.promote_capture(brave_groups(), state)[0].role, oc.Role.MEETING)

    def test_pipewire_capture_without_identity_is_uncertain_and_protects_brave(self):
        runner = FakeRunner(
            {
                ("pw-dump",): """
                    [{"info": {"props": {
                        "media.class": "Stream/Input/Audio"
                    }}}]
                """,
            },
            fail={"pactl"},
        )
        state = oc.capture_state(runner)
        self.assertEqual(
            state,
            oc.CaptureState(active=True, brave=False, certain=False),
        )
        self.assertEqual(oc.promote_capture(brave_groups(), state)[0].role, oc.Role.MEETING)

    def test_failed_capture_probes_are_uncertain_and_inactive(self):
        self.assertEqual(
            oc.capture_state(FakeRunner(fail={"pactl", "pw-dump"})),
            oc.CaptureState(active=False, brave=False, certain=False),
        )


class CapabilityTest(unittest.TestCase):
    def test_network_capability_is_basic_without_helper(self):
        caps = {
            cap.name: cap
            for cap in oc.detect_capabilities(
                FakeRunner(missing={"nothing-optimizer-helper"})
            )
        }
        self.assertEqual(caps["network"].level, "fallback")
        self.assertIn("basic", caps["network"].detail.lower())

    def test_network_requires_typed_active_rule_proof(self):
        command = ("nothing-optimizer-helper", "--capabilities")
        for output in ("", '{"network_rules_active": false}', "[]"):
            with self.subTest(output=output):
                caps = {
                    cap.name: cap
                    for cap in oc.detect_capabilities(
                        FakeRunner({command: output})
                    )
                }
                self.assertEqual(caps["network"].level, "fallback")
                self.assertIn("basic", caps["network"].detail.lower())

    def test_network_full_requires_typed_active_rule_proof(self):
        caps = {
            cap.name: cap
            for cap in oc.detect_capabilities(FakeRunner({
                ("nothing-optimizer-helper", "--capabilities"):
                    '{"network_rules_active": true}',
            }))
        }
        self.assertEqual(caps["network"].level, "full")

    def test_power_failure_is_reported_not_raised(self):
        caps = {
            cap.name: cap
            for cap in oc.detect_capabilities(FakeRunner(fail={"powerprofilesctl"}))
        }
        self.assertEqual(caps["power"].level, "unavailable")

    def test_capability_levels_are_limited_to_the_honest_contract(self):
        caps = oc.detect_capabilities(FakeRunner())
        self.assertTrue({cap.level for cap in caps} <= {"full", "fallback", "unavailable"})


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
            proc(64, 1, "python", "/usr/bin/python3", "/user/app/guard", self.uid,
                 cmdline=("/usr/bin/python3", "/opt/nothing/optimizer_guard.py")),
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

    def test_python_launched_widget_script_is_protected_by_cmdline_path(self):
        rows = (
            proc(65, 1, "python", "/usr/bin/python3", "/user/app/widget", self.uid,
                 cmdline=("/usr/bin/python3", "/opt/nothing/nothing-widgets.py")),
        )
        group = oc.classify_groups(rows, uid=self.uid)[0]
        self.assertEqual(group.role, oc.Role.PROTECTED)

    def test_python_option_arguments_do_not_hide_protected_widget_script(self):
        rows = (
            proc(
                66, 1, "python", "/usr/bin/python3", "/user/app/widget", self.uid,
                cmdline=(
                    "/usr/bin/python3", "-W", "ignore", "-X", "dev",
                    "/opt/nothing/nothing-widgets.py",
                ),
            ),
        )
        group = oc.classify_groups(rows, uid=self.uid)[0]
        self.assertEqual(group.role, oc.Role.PROTECTED)
        self.assertTrue(group.managed)

    def test_ambiguous_python_invocation_is_not_a_managed_background_target(self):
        rows = (
            proc(
                67, 1, "python", "/usr/bin/python3", "/user/app/widget", self.uid,
                cmdline=(
                    "/usr/bin/python3", "--unknown-option", "value",
                    "/opt/nothing/nothing-widgets.py",
                ),
            ),
        )
        group = oc.classify_groups(rows, uid=self.uid)[0]
        self.assertFalse(group.managed)
        self.assertEqual(group.role, oc.Role.BACKGROUND)

    def test_agent_tree_with_uncertain_descendant_cgroup_is_unmanaged_singletons(self):
        rows = (
            proc(80, 1, "claude", "/usr/bin/claude", "/user/app/term", self.uid),
            proc(81, 80, "python", "/usr/bin/python3", "", self.uid),
            proc(82, 81, "java", "/usr/bin/java", "/user/app/term", self.uid),
        )
        groups = oc.classify_groups(rows, uid=self.uid)
        agent_groups = [
            group for group in groups
            if any(process.identity.pid in {80, 81, 82} for process in group.processes)
        ]
        self.assertTrue(all(group.role == oc.Role.PRIMARY for group in agent_groups))
        self.assertTrue(all(not group.managed for group in agent_groups))
        self.assertTrue(all(group.reason == "agent tree has unresolved cgroup"
                            for group in agent_groups))
        self.assertEqual(
            [{process.identity.pid for process in group.processes} for group in agent_groups],
            [{80}, {81}, {82}],
        )

    def test_agent_tree_with_uncertain_descendant_identity_is_unmanaged_singletons(self):
        rows = (
            proc(83, 1, "codex", "/usr/bin/codex", "/user/app/term", self.uid),
            proc(84, 83, "python", "/usr/bin/python3", "/user/app/term", self.uid, start=0),
            proc(85, 84, "java", "/usr/bin/java", "/user/app/term", self.uid),
        )
        groups = oc.classify_groups(rows, uid=self.uid)
        agent_groups = [
            group for group in groups
            if any(process.identity.pid in {83, 84, 85} for process in group.processes)
        ]
        self.assertTrue(all(group.role == oc.Role.PRIMARY for group in agent_groups))
        self.assertTrue(all(not group.managed for group in agent_groups))
        self.assertTrue(all(group.reason == "agent tree has unresolved identity"
                            for group in agent_groups))
        self.assertEqual(
            [{process.identity.pid for process in group.processes} for group in agent_groups],
            [{83}, {84}, {85}],
        )

    def test_nested_agent_roots_are_separate_and_own_closest_descendants(self):
        rows = (
            proc(100, 1, "claude", "/usr/bin/claude", "/user/app/outer", self.uid),
            proc(101, 100, "python", "/usr/bin/python3", "/user/app/outer", self.uid),
            proc(102, 101, "codex", "/usr/bin/codex", "/user/app/inner", self.uid),
            proc(103, 102, "java", "/usr/bin/java", "/user/app/inner", self.uid),
            proc(104, 101, "ruby", "/usr/bin/ruby", "/user/app/outer", self.uid),
        )
        groups = oc.classify_groups(rows, uid=self.uid)
        agent_groups = [group for group in groups if group.role == oc.Role.PRIMARY]
        self.assertEqual(
            [{process.identity.pid for process in group.processes} for group in agent_groups],
            [{100, 101, 104}, {102, 103}],
        )

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

    def test_read_processes_keeps_python_script_path_from_cmdline(self):
        with tempfile.TemporaryDirectory() as proc_root:
            self._write_proc_fixture(
                proc_root, 105, "python", 1, 12346, self.uid, "/usr/bin/python3",
                "/user/app/widget", 17,
                cmdline=("/usr/bin/python3", "/opt/nothing/nothing-widgets.py"),
            )
            processes = oc.read_processes(proc_root=proc_root, uid=self.uid)

        self.assertEqual(
            processes[0].cmdline,
            ("/usr/bin/python3", "/opt/nothing/nothing-widgets.py"),
        )
        self.assertEqual(oc.classify_groups(processes, uid=self.uid)[0].role, oc.Role.PROTECTED)

    def _write_proc_fixture(self, root, pid, comm, ppid, start_time, uid, exe, cgroup, rss_pages,
                            cmdline=()):
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
        if cmdline:
            with open(os.path.join(directory, "cmdline"), "wb") as fh:
                fh.write(b"\0".join(arg.encode("utf-8") for arg in cmdline) + b"\0")


_APP_TEST_PID = 30_000


def group_with_role(role, unit=None):
    """Return one current-user process in an isolated managed application scope."""
    global _APP_TEST_PID
    _APP_TEST_PID += 1
    pid = _APP_TEST_PID
    return oc.AppGroup(
        key="app-%d" % pid,
        label="Test application %d" % pid,
        role=role,
        processes=(proc(pid, 1, "test-app", "/usr/bin/test-app",
                        "/user/app-%d.scope" % pid, uid=1000),),
        unit=unit if unit is not None else "app-%d.scope" % pid,
    )


def controller_with_fakes(runner=None, source=None, journal=None, lease=None,
                          groups_provider=None):
    """Build an app controller that can target only generated fake identities."""
    signals = []
    clock = FakeClock()
    source = source or CurrentFakeProcessSource({})

    def send_signal(identity, signum):
        signals.append((identity, signum))
        if hasattr(source, "states"):
            if signum == signal.SIGSTOP:
                source.states[identity.pid] = "T"
            elif signum == signal.SIGCONT:
                source.states[identity.pid] = "R"
        return True

    kwargs = {}
    if groups_provider is not None:
        kwargs["groups_provider"] = groups_provider
    controller = oc.AppController(
        runner=runner or FakeRunner(
            fail_prefixes={("systemctl", "--user", "freeze")},
        ),
        uid=1000,
        source=source,
        journal=journal or oc.MemoryJournal(),
        lease=lease or FakeLease(),
        send_signal=send_signal,
        clock=clock,
        sleeper=clock.sleep,
        **kwargs
    )
    controller.signal_records = signals
    return controller


class AppControllerTest(unittest.TestCase):
    def test_protected_group_cannot_be_paused_or_ended(self):
        for role in (oc.Role.PRIMARY, oc.Role.MEETING, oc.Role.PROTECTED):
            with self.subTest(role=role):
                controller = controller_with_fakes()
                group = group_with_role(role)
                self.assertTrue(controller.pause(group).protected)
                self.assertTrue(controller.end(group).protected)
                self.assertTrue(controller.force(group, end_token="unissued").protected)
                self.assertEqual(controller.runner.commands, [])

    def test_force_requires_a_failed_end_token_for_the_same_group(self):
        controller = controller_with_fakes()
        group = group_with_role(oc.Role.BACKGROUND)
        result = controller.force(group, end_token="unissued")
        self.assertEqual(result.changed, 0)
        self.assertIn("graceful end", result.detail.lower())

    def test_pause_records_only_identities_it_changed(self):
        controller = controller_with_fakes()
        group = group_with_role(oc.Role.BACKGROUND)
        result = controller.pause(group)
        self.assertEqual(result.changed, len(group.processes))
        self.assertEqual(set(controller.paused), {group.key})
        self.assertEqual(
            controller.paused[group.key].identities,
            tuple(process.identity for process in group.processes),
        )
        self.assertEqual(
            controller.signal_records,
            [(group.processes[0].identity, signal.SIGSTOP)],
        )

    def test_pause_and_resume_prefer_the_exact_managed_user_unit(self):
        group = group_with_role(oc.Role.INTERACTIVE)
        freezer = ("systemctl", "--user", "show", group.unit, "--property=FreezerState")
        controller = controller_with_fakes(
            runner=FakeRunner({freezer: "FreezerState=running"}),
            groups_provider=lambda: (group,),
        )
        self.assertEqual(controller.pause(group).changed, 1)
        self.assertIn(("systemctl", "--user", "freeze", group.unit), controller.runner.commands)
        self.assertEqual(controller.resume(group.key).changed, 1)
        self.assertIn(("systemctl", "--user", "thaw", group.unit), controller.runner.commands)
        self.assertEqual(controller.signal_records, [])

    def test_deep_focus_never_freezes_a_primary_sibling_in_a_shared_unit(self):
        unit = "app-shared.scope"
        background = replace(group_with_role(oc.Role.BACKGROUND), unit=unit)
        primary = replace(group_with_role(oc.Role.PRIMARY), unit=unit)
        controller = controller_with_fakes(
            runner=FakeRunner({
                ("systemctl", "--user", "show", unit, "--property=FreezerState"):
                    "FreezerState=running",
            }),
            groups_provider=lambda: (background, primary),
        )
        controller.set_deep_focus((background, primary), True)
        self.assertNotIn(("systemctl", "--user", "freeze", unit), controller.runner.commands)
        self.assertIn((background.processes[0].identity, signal.SIGSTOP), controller.signal_records)
        self.assertNotIn((primary.processes[0].identity, signal.SIGSTOP), controller.signal_records)

    def test_stale_known_groups_cannot_authorize_a_shared_unit_freeze(self):
        unit = "app-stale-shared.scope"
        background = replace(group_with_role(oc.Role.BACKGROUND), unit=unit)
        primary = replace(group_with_role(oc.Role.PRIMARY), unit=unit)
        runner = FakeRunner({
            ("systemctl", "--user", "show", unit, "--property=FreezerState"):
                "FreezerState=running",
        })
        controller = controller_with_fakes(
            runner=runner,
            groups_provider=lambda: (background, primary),
        )
        self.assertEqual(controller.pause(background, known_groups=(background,)).changed, 1)
        self.assertNotIn(("systemctl", "--user", "freeze", unit), runner.commands)
        self.assertIn((background.processes[0].identity, signal.SIGSTOP), controller.signal_records)

    def test_end_never_invokes_systemctl_stop(self):
        group = group_with_role(oc.Role.BACKGROUND)
        controller = controller_with_fakes(runner=FakeRunner())
        self.assertEqual(controller.end(group).changed, 1)
        self.assertFalse(any(command[:3] == ("systemctl", "--user", "stop")
                             for command in controller.runner.commands))
        self.assertIn((group.processes[0].identity, signal.SIGTERM), controller.signal_records)

    def test_failed_unit_thaw_keeps_controller_owned_pause_pending(self):
        group = group_with_role(oc.Role.BACKGROUND)
        freezer = ("systemctl", "--user", "show", group.unit, "--property=FreezerState")
        runner = FakeRunner(
            {freezer: "FreezerState=running"},
            fail_prefixes={("systemctl", "--user", "thaw")},
        )
        controller = controller_with_fakes(
            runner=runner,
            groups_provider=lambda: (group,),
        )
        self.assertEqual(controller.pause(group).changed, 1)
        self.assertEqual(controller.resume(group.key).changed, 0)
        self.assertIn(group.key, controller.paused)
        self.assertNotIn((group.processes[0].identity, signal.SIGCONT), controller.signal_records)
        entries, _capability = controller.journal.load()
        self.assertEqual(entries["entries"][0]["backend"], "systemd")

    def test_unit_thaw_requires_fresh_exclusive_managed_ownership(self):
        unit = "app-exclusive.scope"
        group = replace(group_with_role(oc.Role.BACKGROUND), unit=unit)
        sibling = replace(group_with_role(oc.Role.PRIMARY), unit=unit)
        groups = [group]
        runner = FakeRunner({
            ("systemctl", "--user", "show", unit, "--property=FreezerState"):
                "FreezerState=running",
        })
        controller = controller_with_fakes(runner=runner, groups_provider=lambda: tuple(groups))
        self.assertEqual(controller.pause(group).changed, 1)
        groups.append(sibling)
        self.assertEqual(controller.resume(group.key).changed, 0)
        self.assertIn(group.key, controller.paused)
        self.assertNotIn(("systemctl", "--user", "thaw", unit), runner.commands)

    def test_restore_interrupted_does_not_thaw_a_unit_with_a_primary_sibling(self):
        unit = "app-recovery-shared.scope"
        group = replace(group_with_role(oc.Role.BACKGROUND), unit=unit)
        sibling = replace(group_with_role(oc.Role.PRIMARY), unit=unit)
        identity = group.processes[0].identity
        journal = oc.MemoryJournal({
            "version": 1,
            "entries": [{
                "action": "pause",
                "group_key": group.key,
                "identities": [{"pid": identity.pid, "start_time": identity.start_time}],
                "unit": unit,
                "backend": "systemd",
                "original": {"state": "running"},
                "owned": {"state": "frozen"},
            }],
        })
        runner = FakeRunner()
        controller = controller_with_fakes(
            runner=runner,
            journal=journal,
            source=CurrentFakeProcessSource({identity.pid: identity.start_time}),
            groups_provider=lambda: (group, sibling),
        )
        self.assertEqual(controller.restore_interrupted()[0].level, "unavailable")
        self.assertNotIn(("systemctl", "--user", "thaw", unit), runner.commands)
        entries, _capability = journal.load()
        self.assertEqual(len(entries["entries"]), 1)

    def test_pause_never_claims_an_already_frozen_unit(self):
        group = group_with_role(oc.Role.BACKGROUND)
        freezer = ("systemctl", "--user", "show", group.unit, "--property=FreezerState")
        controller = controller_with_fakes(
            runner=FakeRunner({freezer: "FreezerState=frozen"}),
            groups_provider=lambda: (group,),
        )
        self.assertEqual(controller.pause(group).changed, 0)
        self.assertNotIn(group.key, controller.paused)
        self.assertEqual(controller.signal_records, [])

    def test_pause_fails_closed_when_a_unit_freezer_state_is_unreadable(self):
        group = group_with_role(oc.Role.BACKGROUND)
        freezer = ("systemctl", "--user", "show", group.unit, "--property=FreezerState")
        controller = controller_with_fakes(
            runner=FakeRunner(fail_prefixes={freezer}),
            groups_provider=lambda: (group,),
        )
        self.assertEqual(controller.pause(group).changed, 0)
        self.assertNotIn(group.key, controller.paused)
        self.assertEqual(controller.signal_records, [])

    def test_pause_does_not_mutate_when_guard_or_journal_registration_fails(self):
        group = group_with_role(oc.Role.BACKGROUND, unit="")
        for lease, journal in ((FakeLease(fail_track=True), oc.MemoryJournal()),
                               (FakeLease(), FailingJournal())):
            with self.subTest(lease=lease.fail_track):
                controller = controller_with_fakes(lease=lease, journal=journal)
                self.assertEqual(controller.pause(group).changed, 0)
                self.assertEqual(controller.signal_records, [])
                self.assertNotIn(group.key, controller.paused)

    def test_pause_never_claims_an_already_stopped_process(self):
        group = group_with_role(oc.Role.BACKGROUND, unit="")
        source = CurrentFakeProcessSource(
            {group.processes[0].identity.pid: group.processes[0].identity.start_time},
            {group.processes[0].identity.pid: "T"},
        )
        controller = controller_with_fakes(source=source)
        self.assertEqual(controller.pause(group).changed, 0)
        self.assertNotIn(group.key, controller.paused)
        self.assertEqual(controller.signal_records, [])

    def test_pause_refuses_an_unknown_process_state(self):
        group = group_with_role(oc.Role.BACKGROUND, unit="")
        identity = group.processes[0].identity
        source = CurrentFakeProcessSource(
            {identity.pid: identity.start_time}, {identity.pid: None})
        controller = controller_with_fakes(source=source)
        self.assertEqual(controller.pause(group).changed, 0)
        self.assertNotIn(group.key, controller.paused)
        self.assertEqual(controller.signal_records, [])

    def test_resume_retains_an_unknown_process_state(self):
        group = group_with_role(oc.Role.BACKGROUND, unit="")
        identity = group.processes[0].identity
        source = CurrentFakeProcessSource(
            {identity.pid: identity.start_time}, {identity.pid: None})
        controller = controller_with_fakes(source=source)
        controller.paused[group.key] = oc.PausedGroup(
            group.key, "", (identity,), "process")
        result = controller.resume(group.key)
        self.assertEqual(result.survived, 1)
        self.assertIn(group.key, controller.paused)
        self.assertEqual(controller.signal_records, [])

    def test_resume_discards_all_stale_unit_identities_and_allows_repause(self):
        unit = "app-stale-unit.scope"
        group = replace(group_with_role(oc.Role.BACKGROUND), unit=unit)
        identity = group.processes[0].identity
        source = CurrentFakeProcessSource({identity.pid: identity.start_time})
        lease = FakeLease()
        groups = [group]
        runner = FakeRunner({
            ("systemctl", "--user", "show", unit, "--property=FreezerState"):
                "FreezerState=running",
        })
        controller = controller_with_fakes(
            runner=runner, source=source, lease=lease,
            groups_provider=lambda: tuple(groups),
        )
        self.assertEqual(controller.pause(group).changed, 1)
        source.start_times[identity.pid] = identity.start_time + 1
        self.assertEqual(controller.resume(group.key).survived, 0)
        self.assertNotIn(group.key, controller.paused)
        self.assertIn(identity, lease.untracked)
        entries, _capability = controller.journal.load()
        self.assertEqual(entries["entries"], [])
        replacement_identity = oc.ProcessIdentity(identity.pid, identity.start_time + 1)
        source.states[identity.pid] = "R"
        replacement = replace(group, processes=(replace(
            group.processes[0], identity=replacement_identity),))
        groups[:] = [replacement]
        self.assertEqual(controller.pause(replacement).changed, 1)

    def test_partial_pause_rollback_retains_a_still_stopped_identity(self):
        first = proc(9101, 1, "test-app", "/usr/bin/test-app", "", uid=1000)
        second = proc(9102, 1, "test-app", "/usr/bin/test-app", "", uid=1000)
        group = oc.AppGroup("rollback", "Rollback", oc.Role.BACKGROUND,
                            (first, second), unit="")
        states = {first.identity.pid: "R", second.identity.pid: "R"}
        source = CurrentFakeProcessSource({
            first.identity.pid: first.identity.start_time,
            second.identity.pid: second.identity.start_time,
        }, states)
        signals = []

        def send_signal(identity, signum):
            signals.append((identity, signum))
            if identity == first.identity and signum == signal.SIGSTOP:
                states[identity.pid] = "T"
                return True
            if identity == second.identity and signum == signal.SIGSTOP:
                return False
            if identity == first.identity and signum == signal.SIGCONT:
                return False
            return True

        lease = FakeLease()
        journal = oc.MemoryJournal()
        controller = oc.AppController(
            runner=FakeRunner(), uid=1000, source=source, journal=journal,
            lease=lease, send_signal=send_signal,
        )
        result = controller.pause(group)
        self.assertEqual(result.changed, 0)
        self.assertEqual(result.survived, 1)
        self.assertEqual(controller.paused[group.key].identities, (first.identity,))
        self.assertIn((first.identity, signal.SIGCONT), signals)
        self.assertNotIn(first.identity, lease.untracked)
        self.assertIn(second.identity, lease.untracked)
        entries, _capability = journal.load()
        self.assertEqual(entries["entries"][0]["identities"], [
            {"pid": first.identity.pid, "start_time": first.identity.start_time},
        ])

    def test_successful_pause_rollback_removes_confirmed_ownership(self):
        first = proc(9111, 1, "test-app", "/usr/bin/test-app", "", uid=1000)
        second = proc(9112, 1, "test-app", "/usr/bin/test-app", "", uid=1000)
        group = oc.AppGroup("rollback-clear", "Rollback clear", oc.Role.BACKGROUND,
                            (first, second), unit="")
        states = {first.identity.pid: "R", second.identity.pid: "R"}
        source = CurrentFakeProcessSource({
            first.identity.pid: first.identity.start_time,
            second.identity.pid: second.identity.start_time,
        }, states)

        def send_signal(identity, signum):
            if identity == first.identity and signum == signal.SIGSTOP:
                states[identity.pid] = "T"
                return True
            if identity == second.identity and signum == signal.SIGSTOP:
                return False
            if identity == first.identity and signum == signal.SIGCONT:
                states[identity.pid] = "R"
                return True
            return True

        journal = oc.MemoryJournal()
        controller = oc.AppController(
            runner=FakeRunner(), uid=1000, source=source, journal=journal,
            lease=FakeLease(), send_signal=send_signal,
        )
        self.assertEqual(controller.pause(group).changed, 0)
        self.assertNotIn(group.key, controller.paused)
        entries, _capability = journal.load()
        self.assertEqual(entries["entries"], [])

    def test_uncertain_freeze_retains_the_prepared_recovery_record(self):
        group = group_with_role(oc.Role.BACKGROUND)
        freezer = ("systemctl", "--user", "show", group.unit, "--property=FreezerState")
        runner = FakeRunner(
            {freezer: ["FreezerState=running", "FreezerState=frozen"]},
            fail_prefixes={("systemctl", "--user", "freeze")},
        )
        lease = FakeLease()
        controller = controller_with_fakes(
            runner=runner, lease=lease, groups_provider=lambda: (group,))
        result = controller.pause(group)
        self.assertEqual(result.changed, 0)
        self.assertEqual(result.survived, 1)
        self.assertIn(group.key, controller.paused)
        self.assertNotIn(group.processes[0].identity, lease.untracked)
        entries, _capability = controller.journal.load()
        self.assertEqual(entries["entries"][0]["backend"], "systemd-uncertain")

    def test_uncertain_freeze_recovery_never_thaws_an_unconfirmed_unit(self):
        group = group_with_role(oc.Role.BACKGROUND)
        freezer = ("systemctl", "--user", "show", group.unit, "--property=FreezerState")
        runner = FakeRunner(
            {freezer: ["FreezerState=running", "FreezerState=frozen"]},
            fail_prefixes={("systemctl", "--user", "freeze")},
        )
        controller = controller_with_fakes(
            runner=runner, lease=FakeLease(), groups_provider=lambda: (group,))
        self.assertEqual(controller.pause(group).survived, 1)

        result = controller.restore_interrupted()

        self.assertEqual(result[0].level, "unavailable")
        self.assertNotIn(("systemctl", "--user", "thaw", group.unit), runner.commands)
        entries, _capability = controller.journal.load()
        self.assertEqual(entries["entries"][0]["backend"], "systemd-uncertain")

    def test_confirmed_running_after_failed_freeze_clears_the_prepared_record(self):
        group = group_with_role(oc.Role.BACKGROUND)
        freezer = ("systemctl", "--user", "show", group.unit, "--property=FreezerState")
        runner = FakeRunner(
            {freezer: ["FreezerState=running", "FreezerState=running"]},
            fail_prefixes={("systemctl", "--user", "freeze")},
        )
        lease = FakeLease()
        controller = controller_with_fakes(
            runner=runner, lease=lease, groups_provider=lambda: (group,))
        self.assertEqual(controller.pause(group).changed, 0)
        self.assertNotIn(group.key, controller.paused)
        self.assertIn(group.processes[0].identity, lease.untracked)
        entries, _capability = controller.journal.load()
        self.assertEqual(entries["entries"], [])

    def test_actions_skip_a_reused_process_identity_before_any_signal(self):
        group = group_with_role(oc.Role.BACKGROUND, unit="")
        signals = []
        controller = oc.AppController(
            runner=FakeRunner(),
            uid=1000,
            source=FakeProcessSource({group.processes[0].identity.pid:
                                      group.processes[0].identity.start_time + 1}),
            journal=oc.MemoryJournal(),
            send_signal=lambda identity, signum: signals.append((identity, signum)) or True,
        )
        self.assertEqual(controller.pause(group).changed, 0)
        self.assertEqual(controller.end(group).changed, 0)
        self.assertEqual(signals, [])

    def test_end_falls_back_to_graceful_signal_when_window_close_is_unavailable(self):
        controller = controller_with_fakes()
        group = replace(group_with_role(oc.Role.BACKGROUND, unit=""), desktop_id="test.desktop")

        def unavailable_window_close(_group):
            raise RuntimeError("desktop API unavailable")

        controller.desktop_close = unavailable_window_close
        self.assertEqual(controller.end(group).changed, 1)
        self.assertIn((group.processes[0].identity, signal.SIGTERM), controller.signal_records)

    def test_modes_have_no_reference_to_end_or_force(self):
        source = inspect.getsource(oc.ResourceController.apply)
        self.assertNotIn(".end(", source)
        self.assertNotIn(".force(", source)

    def test_deep_focus_pauses_only_background_groups(self):
        controller = controller_with_fakes()
        groups = tuple(group_with_role(role) for role in oc.Role)
        controller.set_deep_focus(groups, True)
        self.assertEqual(
            set(controller.paused),
            {group.key for group in groups if group.role == oc.Role.BACKGROUND},
        )

    def test_disabling_deep_focus_leaves_manual_pause_untouched(self):
        controller = controller_with_fakes()
        manual = group_with_role(oc.Role.BACKGROUND)
        focus = group_with_role(oc.Role.BACKGROUND)
        self.assertEqual(controller.pause(manual).changed, 1)
        controller.set_deep_focus((manual, focus), True)
        controller.set_deep_focus((manual, focus), False)
        self.assertEqual(set(controller.paused), {manual.key})
        self.assertIn((focus.processes[0].identity, signal.SIGCONT), controller.signal_records)

    def test_deep_focus_does_not_resume_a_later_manual_pause_instance(self):
        controller = controller_with_fakes()
        group = group_with_role(oc.Role.BACKGROUND, unit="")
        controller.set_deep_focus((group,), True)
        self.assertEqual(controller.resume(group.key).changed, 1)
        self.assertEqual(controller.pause(group).changed, 1)
        before = list(controller.signal_records)
        controller.set_deep_focus((group,), False)
        self.assertEqual(set(controller.paused), {group.key})
        self.assertEqual(controller.signal_records, before)

    def test_resume_discards_stale_identity_and_allows_a_fresh_pause(self):
        group = group_with_role(oc.Role.BACKGROUND, unit="")
        source = CurrentFakeProcessSource({})
        lease = FakeLease()
        controller = controller_with_fakes(source=source, lease=lease)
        self.assertEqual(controller.pause(group).changed, 1)
        identity = group.processes[0].identity
        source.start_times[identity.pid] = identity.start_time + 1
        self.assertEqual(controller.resume(group.key).changed, 0)
        self.assertNotIn(group.key, controller.paused)
        self.assertIn(identity, lease.untracked)
        replacement_identity = oc.ProcessIdentity(identity.pid, identity.start_time + 1)
        source.states[identity.pid] = "R"
        replacement = replace(group, processes=(replace(
            group.processes[0], identity=replacement_identity),))
        self.assertEqual(controller.pause(replacement).changed, 1)

    def test_restore_interrupted_uses_injected_journal_runner_and_signal(self):
        group = group_with_role(oc.Role.BACKGROUND, unit="")
        identity = group.processes[0].identity
        journal = oc.MemoryJournal({
            "version": 1,
            "entries": [{
                "action": "pause",
                "group_key": group.key,
                "identities": [{"pid": identity.pid, "start_time": identity.start_time}],
                "unit": "",
                "backend": "process",
                "original": {"state": "running"},
                "owned": {"state": "stopped"},
            }],
        })
        controller = controller_with_fakes(
            journal=journal,
            source=CurrentFakeProcessSource({identity.pid: identity.start_time},
                                            {identity.pid: "T"}),
        )
        result = controller.restore_interrupted()
        self.assertEqual(result[0].level, "full")
        self.assertIn((identity, signal.SIGCONT), controller.signal_records)
        entries, _capability = journal.load()
        self.assertEqual(entries["entries"], [])

    def test_force_accepts_only_the_live_token_from_the_same_graceful_end(self):
        controller = controller_with_fakes()
        group = group_with_role(oc.Role.BACKGROUND, unit="")
        other = group_with_role(oc.Role.BACKGROUND, unit="")
        end = controller.end(group)
        self.assertEqual(end.survived, 1)
        self.assertTrue(end.end_token)
        self.assertEqual(controller.force(other, end.end_token).changed, 0)
        self.assertEqual(controller.force(group, end.end_token).changed, 1)
        self.assertIn((group.processes[0].identity, signal.SIGKILL), controller.signal_records)


class RuntimeJournalTest(unittest.TestCase):
    def test_runtime_journal_is_owner_only_atomic_and_corrupt_input_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "optimizer-runtime.json")
            journal = {
                "version": 1,
                "entries": [{
                    "action": "pause",
                    "group_key": "test",
                    "identities": [{"pid": 301, "start_time": 30100}],
                    "unit": "",
                    "backend": "process",
                    "original": {"state": "running"},
                    "owned": {"state": "stopped"},
                }],
            }
            self.assertTrue(oc.save_runtime_journal(path, journal))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertFalse(os.path.exists(path + ".tmp"))
            loaded, capability = oc.load_runtime_journal(path)
            self.assertEqual(loaded, journal)
            self.assertEqual(capability.level, "full")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("not JSON")
            loaded, capability = oc.load_runtime_journal(path)
            self.assertEqual(loaded["entries"], [])
            self.assertEqual(capability.level, "unavailable")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({
                    "version": 1,
                    "entries": [{
                        "action": "resource",
                        "group_key": "test",
                        "identities": [{"pid": 301, "start_time": 30100}],
                        "unit": "",
                        "original": {"io": None},
                        "owned": {"io": [2, 7]},
                    }],
                }, fh)
            loaded, capability = oc.load_runtime_journal(path)
            self.assertEqual(loaded["entries"], [])
            self.assertEqual(capability.level, "unavailable")

    def test_restore_from_journal_rechecks_identity_before_process_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "optimizer-runtime.json")
            identity = oc.ProcessIdentity(321, 32100)
            self.assertTrue(oc.save_runtime_journal(path, {
                "version": 1,
                "entries": [{
                    "action": "resource",
                    "group_key": "test",
                    "identities": [{"pid": identity.pid, "start_time": identity.start_time}],
                    "unit": "",
                    "original": {"cpu": 3},
                    "owned": {"cpu": 9},
                }],
            }))
            runner = FakeRunner({("ps", "-o", "ni=", "-p", "321"): "9"})
            oc.restore_from_journal(
                path=path,
                runner=runner,
                source=CurrentFakeProcessSource({321: 32100}),
            )
            self.assertIn(("renice", "-n", "3", "-p", "321"), runner.commands)
            restored, _capability = oc.load_runtime_journal(path)
            self.assertEqual(restored["entries"], [])

    def test_restore_from_journal_restores_only_owned_user_unit_properties(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "optimizer-runtime.json")
            identity = oc.ProcessIdentity(322, 32200)
            self.assertTrue(oc.save_runtime_journal(path, {
                "version": 1,
                "entries": [{
                    "action": "resource",
                    "group_key": "test",
                    "identities": [{"pid": identity.pid, "start_time": identity.start_time}],
                    "unit": "app-test.scope",
                    "original": {"CPUWeight": "100"},
                    "owned": {"CPUWeight": "200"},
                }],
            }))
            show = ("systemctl", "--user", "show", "app-test.scope",
                    "--property=CPUWeight,IOWeight,MemoryLow")
            runner = FakeRunner({show: "CPUWeight=200\nIOWeight=100\nMemoryLow=0\n"})
            oc.restore_from_journal(
                path=path,
                runner=runner,
                source=CurrentFakeProcessSource({322: 32200}),
            )
            self.assertIn(
                ("systemctl", "--user", "set-property", "--runtime", "app-test.scope",
                 "CPUWeight=100"),
                runner.commands,
            )
            restored, _capability = oc.load_runtime_journal(path)
            self.assertEqual(restored["entries"], [])

    def test_restore_from_journal_thaws_owned_cgroup_pause_without_sigcont(self):
        identity = oc.ProcessIdentity(323, 32300)
        group = oc.AppGroup(
            "test", "Test", oc.Role.BACKGROUND,
            (proc(323, 1, "test-app", "/usr/bin/test-app", "/user/app-test.scope",
                  start=32300),),
            unit="app-test.scope",
        )
        journal = oc.MemoryJournal({
            "version": 1,
            "entries": [{
                "action": "pause",
                "group_key": "test",
                "identities": [{"pid": identity.pid, "start_time": identity.start_time}],
                "unit": "app-test.scope",
                "backend": "systemd",
                "original": {"state": "running"},
                "owned": {"state": "frozen"},
            }],
        })
        runner = FakeRunner(fail_prefixes={("systemctl", "--user", "thaw")})
        signals = []
        result = oc.restore_from_journal(
            runner=runner,
            source=CurrentFakeProcessSource({323: 32300}),
            journal=journal,
            send_signal=lambda process, signum: signals.append((process, signum)) or True,
            groups_provider=lambda: (group,),
        )
        self.assertEqual(result[0].level, "unavailable")
        self.assertIn(("systemctl", "--user", "thaw", "app-test.scope"), runner.commands)
        self.assertEqual(signals, [])
        entries, _capability = journal.load()
        self.assertEqual(len(entries["entries"]), 1)
        runner.fail_prefixes.clear()
        self.assertEqual(oc.restore_from_journal(
            runner=runner,
            source=CurrentFakeProcessSource({323: 32300}),
            journal=journal,
            send_signal=lambda process, signum: signals.append((process, signum)) or True,
            groups_provider=lambda: (group,),
        )[0].level, "full")
        entries, _capability = journal.load()
        self.assertEqual(entries["entries"], [])

    def test_journal_restore_retains_an_unknown_process_state(self):
        identity = oc.ProcessIdentity(325, 32500)
        journal = oc.MemoryJournal({
            "version": 1,
            "entries": [{
                "action": "pause",
                "group_key": "test",
                "identities": [{"pid": identity.pid, "start_time": identity.start_time}],
                "unit": "",
                "backend": "process",
                "original": {"state": "running"},
                "owned": {"state": "stopped"},
            }],
        })
        signals = []
        result = oc.restore_from_journal(
            runner=FakeRunner(),
            source=CurrentFakeProcessSource({325: 32500}, {325: None}),
            journal=journal,
            send_signal=lambda identity, signum: signals.append((identity, signum)) or True,
        )
        self.assertEqual(result[0].level, "unavailable")
        self.assertEqual(signals, [])
        entries, _capability = journal.load()
        self.assertEqual(len(entries["entries"]), 1)

    def test_journal_never_resumes_a_prepared_process_pause(self):
        identity = oc.ProcessIdentity(327, 32700)
        journal = oc.MemoryJournal({
            "version": 1,
            "entries": [{
                "action": "pause",
                "group_key": "test",
                "identities": [{"pid": identity.pid, "start_time": identity.start_time}],
                "unit": "",
                "backend": "process-prepared",
                "original": {"state": "running"},
                "owned": {"state": "prepared"},
            }],
        })
        signals = []
        result = oc.restore_from_journal(
            runner=FakeRunner(),
            source=CurrentFakeProcessSource({327: 32700}, {327: "T"}),
            journal=journal,
            send_signal=lambda identity, signum: signals.append((identity, signum)) or True,
        )
        self.assertEqual(result[0].level, "unavailable")
        self.assertEqual(signals, [])
        entries, _capability = journal.load()
        self.assertEqual(entries["entries"][0]["backend"], "process-prepared")

    def test_journal_restore_retains_systemd_pause_without_live_identities(self):
        identity = oc.ProcessIdentity(326, 32600)
        journal = oc.MemoryJournal({
            "version": 1,
            "entries": [{
                "action": "pause",
                "group_key": "test",
                "identities": [{"pid": identity.pid, "start_time": identity.start_time}],
                "unit": "app-test.scope",
                "backend": "systemd",
                "original": {"state": "running"},
                "owned": {"state": "frozen"},
            }],
        })
        runner = FakeRunner()
        result = oc.restore_from_journal(
            runner=runner,
            source=CurrentFakeProcessSource({326: 32601}),
            journal=journal,
            groups_provider=lambda: (),
        )
        self.assertEqual(result[0].level, "unavailable")
        self.assertNotIn(("systemctl", "--user", "thaw", "app-test.scope"), runner.commands)
        entries, _capability = journal.load()
        self.assertEqual(len(entries["entries"]), 1)

    def test_thaw_reports_degradation_when_the_journal_cannot_clear(self):
        identity = oc.ProcessIdentity(324, 32400)
        group = oc.AppGroup(
            "test", "Test", oc.Role.BACKGROUND,
            (proc(324, 1, "test-app", "/usr/bin/test-app", "/user/app-test.scope",
                  start=32400),),
            unit="app-test.scope",
        )
        journal = FailingReplaceJournal({
            "version": 1,
            "entries": [{
                "action": "pause",
                "group_key": "test",
                "identities": [{"pid": identity.pid, "start_time": identity.start_time}],
                "unit": "app-test.scope",
                "backend": "systemd",
                "original": {"state": "running"},
                "owned": {"state": "frozen"},
            }],
        })
        result = oc.restore_from_journal(
            runner=FakeRunner(),
            source=CurrentFakeProcessSource({324: 32400}),
            journal=journal,
            groups_provider=lambda: (group,),
        )
        self.assertEqual(result[0].level, "unavailable")
        entries, _capability = journal.load()
        self.assertEqual(len(entries["entries"]), 1)


class GuardIntegrationTest(unittest.TestCase):
    def _state(self, pid):
        with open("/proc/%d/stat" % pid, encoding="utf-8") as fh:
            raw = fh.read()
        return raw[raw.rfind(")") + 2:].split()[0]

    def _cleanup_child(self, child):
        if child.poll() is None:
            try:
                os.kill(child.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
            child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)

    def test_guard_eof_never_resumes_a_bare_disposable_sleep_process(self):
        unique_name = "optimizer-guard-test-" + uuid.uuid4().hex
        child = subprocess.Popen([
            "bash", "-c", "exec -a %s sleep 30" % unique_name,
        ])
        self.addCleanup(self._cleanup_child, child)
        command_line = b""
        named_deadline = time.monotonic() + 2
        while time.monotonic() < named_deadline:
            with open("/proc/%d/cmdline" % child.pid, "rb") as fh:
                command_line = fh.read()
            if unique_name.encode("ascii") in command_line:
                break
            time.sleep(0.02)
        self.assertIn(unique_name.encode("ascii"), command_line)
        with open("/proc/%d/stat" % child.pid, encoding="utf-8") as fh:
            _ppid, start_time = oc._parse_stat(fh.read())
        with tempfile.TemporaryDirectory() as config_home:
            environment = os.environ.copy()
            environment["XDG_CONFIG_HOME"] = config_home
            guard = subprocess.Popen(
                [sys.executable, os.path.join(os.path.dirname(__file__), "optimizer_guard.py")],
                stdin=subprocess.PIPE,
                text=True,
                env=environment,
            )
            self.addCleanup(self._cleanup_child, guard)
            guard.stdin.write(json.dumps({
                "op": "track", "pid": child.pid, "start_time": start_time,
            }) + "\n")
            guard.stdin.flush()
            os.kill(child.pid, signal.SIGSTOP)
            stopped_deadline = time.monotonic() + 2
            while self._state(child.pid) != "T" and time.monotonic() < stopped_deadline:
                time.sleep(0.02)
            self.assertEqual(self._state(child.pid), "T")
            guard.stdin.close()
            guard.wait(timeout=3)
            self.assertEqual(self._state(child.pid), "T")

    def test_guard_eof_resumes_a_confirmed_disposable_sleep_process(self):
        unique_name = "optimizer-confirm-test-" + uuid.uuid4().hex
        child = subprocess.Popen([
            "bash", "-c", "exec -a %s sleep 30" % unique_name,
        ])
        self.addCleanup(self._cleanup_child, child)
        with open("/proc/%d/stat" % child.pid, encoding="utf-8") as fh:
            _ppid, start_time = oc._parse_stat(fh.read())
        with tempfile.TemporaryDirectory() as config_home:
            environment = os.environ.copy()
            environment["XDG_CONFIG_HOME"] = config_home
            guard = subprocess.Popen(
                [sys.executable, os.path.join(os.path.dirname(__file__), "optimizer_guard.py")],
                stdin=subprocess.PIPE, text=True, env=environment,
            )
            self.addCleanup(self._cleanup_child, guard)
            for backend in ("process-prepared", "process"):
                if backend == "process":
                    os.kill(child.pid, signal.SIGSTOP)
                    stopped_deadline = time.monotonic() + 2
                    while self._state(child.pid) != "T" and time.monotonic() < stopped_deadline:
                        time.sleep(0.02)
                    self.assertEqual(self._state(child.pid), "T")
                guard.stdin.write(json.dumps({
                    "op": "track", "pid": child.pid, "start_time": start_time,
                    "backend": backend, "unit": "",
                }) + "\n")
                guard.stdin.flush()
            guard.stdin.close()
            guard.wait(timeout=3)
            resumed_deadline = time.monotonic() + 2
            while self._state(child.pid) == "T" and time.monotonic() < resumed_deadline:
                time.sleep(0.02)
            self.assertNotEqual(self._state(child.pid), "T")

    def test_guard_lease_clean_close_resumes_its_disposable_sleep_process(self):
        unique_name = "optimizer-lease-test-" + uuid.uuid4().hex
        child = subprocess.Popen([
            "bash", "-c", "exec -a %s sleep 30" % unique_name,
        ])
        self.addCleanup(self._cleanup_child, child)
        named_deadline = time.monotonic() + 2
        command_line = b""
        while time.monotonic() < named_deadline:
            with open("/proc/%d/cmdline" % child.pid, "rb") as fh:
                command_line = fh.read()
            if unique_name.encode("ascii") in command_line:
                break
            time.sleep(0.02)
        self.assertIn(unique_name.encode("ascii"), command_line)
        with open("/proc/%d/stat" % child.pid, encoding="utf-8") as fh:
            _ppid, start_time = oc._parse_stat(fh.read())
        with tempfile.TemporaryDirectory() as config_home:
            environment = os.environ.copy()
            environment["XDG_CONFIG_HOME"] = config_home
            lease = oc.GuardLease(environment=environment)
            self.addCleanup(lease.close)
            self.assertTrue(lease.track(
                oc.ProcessIdentity(child.pid, start_time),
                backend="process-prepared", unit="app-test.scope",
            ))
            os.kill(child.pid, signal.SIGSTOP)
            stopped_deadline = time.monotonic() + 2
            while self._state(child.pid) != "T" and time.monotonic() < stopped_deadline:
                time.sleep(0.02)
            self.assertEqual(self._state(child.pid), "T")
            self.assertTrue(lease.track(
                oc.ProcessIdentity(child.pid, start_time), backend="process", unit="",
            ))
            lease.close()
            resumed_deadline = time.monotonic() + 2
            while self._state(child.pid) == "T" and time.monotonic() < resumed_deadline:
                time.sleep(0.02)
            self.assertNotEqual(self._state(child.pid), "T")

    def test_guard_restore_thaws_a_confirmed_exclusive_systemd_pause(self):
        identity = oc.ProcessIdentity(328, 32800)
        group = oc.AppGroup(
            "test", "Test", oc.Role.BACKGROUND,
            (proc(328, 1, "test-app", "/usr/bin/test-app", "/user/app-test.scope",
                  start=32800),), unit="app-test.scope",
        )
        journal = oc.MemoryJournal({
            "version": 1,
            "entries": [{
                "action": "pause", "group_key": "test",
                "identities": [{"pid": identity.pid, "start_time": identity.start_time}],
                "unit": "app-test.scope", "backend": "systemd",
                "original": {"state": "running"}, "owned": {"state": "frozen"},
            }],
        })
        runner = FakeRunner()

        def restore(**kwargs):
            return oc.restore_from_journal(
                runner=runner,
                source=CurrentFakeProcessSource({328: 32800}),
                journal=journal,
                **kwargs
            )

        og.main(io.StringIO(""), groups_provider=lambda: (group,),
                restore_from_journal=restore)
        self.assertEqual(runner.commands.count(
            ("systemctl", "--user", "thaw", "app-test.scope")), 1)

    def test_guard_restore_retains_systemd_pause_without_exclusive_proof(self):
        identity = oc.ProcessIdentity(329, 32900)
        group = oc.AppGroup(
            "test", "Test", oc.Role.BACKGROUND,
            (proc(329, 1, "test-app", "/usr/bin/test-app", "/user/app-test.scope",
                  start=32900),), unit="app-test.scope",
        )
        sibling = replace(group_with_role(oc.Role.PRIMARY), unit="app-test.scope")
        journal = oc.MemoryJournal({
            "version": 1,
            "entries": [{
                "action": "pause", "group_key": "test",
                "identities": [{"pid": identity.pid, "start_time": identity.start_time}],
                "unit": "app-test.scope", "backend": "systemd",
                "original": {"state": "running"}, "owned": {"state": "frozen"},
            }],
        })
        runner = FakeRunner()

        def restore(**kwargs):
            return oc.restore_from_journal(
                runner=runner,
                source=CurrentFakeProcessSource({329: 32900}),
                journal=journal,
                **kwargs
            )

        og.main(io.StringIO(""), groups_provider=lambda: (group, sibling),
                restore_from_journal=restore)
        self.assertNotIn(("systemctl", "--user", "thaw", "app-test.scope"), runner.commands)
        entries, _capability = journal.load()
        self.assertEqual(len(entries["entries"]), 1)
