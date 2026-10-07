import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
HELPER = ROOT / "root-helper" / "guardian-root-helper"


@pytest.mark.parametrize("args", [[], ["rm"], ["apt-clean", "extra"], ["--help"], ["apt-clean;reboot"]])
def test_helper_rejects_anything_but_three_verbs(args):
    p = subprocess.run(["bash", str(HELPER), *args], capture_output=True, text=True)
    assert p.returncode == 2 and "usage" in p.stderr


def test_scripts_parse():
    for script in (HELPER, ROOT / "install.sh", ROOT / "uninstall.sh"):
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0, script


def test_polkit_rule_pins_program_and_user_placeholder():
    rule = (ROOT / "root-helper" / "50-guardian.rules").read_text()
    assert '"/usr/local/sbin/guardian-root-helper"' in rule and "__USER__" in rule


def test_service_is_resource_bounded():
    unit = (ROOT / "systemd" / "guardian.service").read_text()
    for line in ("MemoryMax=600M", "Nice=10", "IOSchedulingClass=idle", "Type=oneshot"):
        assert line in unit


def test_beta_script_parses():
    assert subprocess.run(["bash", "-n", str(ROOT / "beta" / "daily.sh")]).returncode == 0


def test_beta_verify_parses():
    assert subprocess.run(["bash", "-n", str(ROOT / "beta" / "verify.sh")]).returncode == 0
