#!/usr/bin/env python3
"""Tests for the display-independent AGENT WORK optimizer core."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import optimizer_core as oc


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
