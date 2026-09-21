# Adaptive Optimizer Network QoS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional, verified per-application egress priority so agent work and Brave meetings win local upload contention without giving the desktop widget arbitrary root access or disturbing pre-existing network policy.

**Architecture:** Install a root-owned Python helper with three exact commands—`probe`, `apply`, and `restore`—authorized separately through sudoers. The helper validates current-user cgroup paths, discovers the active default-route interface itself, marks packets by cgroup through a namespaced nftables output chain, and routes those marks into high/normal/low `tc` priority bands with `fq_codel` children. The optimizer core treats this as a pluggable backend and displays `NETWORK · PRIORITIZED` only after a post-apply probe succeeds; otherwise it retains `NETWORK · BASIC`.

**Tech Stack:** Python 3.12 standard library, Linux cgroup v2, nftables, `tc`/iproute2, systemd user cgroups, sudoers, stdlib `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-14-adaptive-optimizer-design.md`

## Global Constraints

- Execute this plan after `2026-09-14-adaptive-optimizer-core.md`; it consumes the core `NetworkBackend` and `Capability` interfaces.
- The helper is optional. A plain install leaves the complete optimizer functional with `NETWORK · BASIC`.
- Optimize egress scheduling only; do not claim control over ISP/router queues or inbound bandwidth.
- The helper discovers the interface; user input cannot name an interface, path, executable, or command.
- The caller and all cgroups must belong to the active desktop UID below its cgroup-v2 user hierarchy.
- Accept only `high`, `normal`, and `low` classes from the typed stdin document.
- Never use `shell=True` or interpolate policy values into a shell command.
- Refuse loopback, virtual interfaces without a default route, non-cgroup-v2 hosts, unsupported nft syntax, and non-default/custom root qdiscs.
- Add and remove only helper-owned nftables tables and qdisc handle `1:` recorded in root-owned state.
- On partial apply, roll back every helper-owned object before returning failure.
- Automated tests use fake runners and temporary cgroup trees; they never use sudo, nft netlink mutation, or a real network interface.
- The working tree is dirty. Never stage desktop-state, panel, Vicinae, `.superpowers/`, Speed Test, or Now Playing changes.

## File Structure

- Create `nothing-widgets/optimizer_helper.py`: privileged CLI, schema validation, capability probe, command planning, apply/restore transaction, and root-owned state.
- Create `nothing-widgets/test_optimizer_helper.py`: helper validation, command-plan, ownership, rollback, and CLI contract tests.
- Modify `nothing-widgets/optimizer_core.py`: `PrivilegedNetworkBackend` client and helper capability integration.
- Modify `nothing-widgets/test_optimizer_core.py`: client success/fallback tests.
- Create `config/nothing-optimizer.sudoers`: exact no-argument-extra authorizations for `probe`, `apply`, and `restore`.
- Modify `install.sh`: opt-in root-owned helper and validated sudoers installation.
- Modify `README.md`: network scope, capability labels, install, validation, and removal.

---

### Task 1: Validate the privileged helper input and ownership boundary

**Files:**
- Create: `nothing-widgets/optimizer_helper.py`
- Create: `nothing-widgets/test_optimizer_helper.py`

**Interfaces:**
- Consumes: JSON from stdin and `SUDO_UID`
- Produces: `NetworkRequest`, `ValidatedGroup`, `parse_request(raw, caller_uid, cgroup_root) -> NetworkRequest`, `validate_cgroup(path, uid, cgroup_root) -> ValidatedGroup`

- [ ] **Step 1: Write schema and path-validation tests**

```python
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import optimizer_helper as oh


class RequestValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.user_root = os.path.join(
            self.root, "user.slice", "user-1000.slice", "user@1000.service")
        self.agent = os.path.join(self.user_root, "app.slice", "agent.scope")
        os.makedirs(self.agent)
        self.addCleanup(self.tmp.cleanup)

    def request(self, groups):
        return json.dumps({"version": 1, "groups": groups})

    def test_accepts_current_user_cgroup_and_fixed_class(self):
        req = oh.parse_request(
            self.request([{"cgroup": self.agent, "class": "high"}]),
            caller_uid=1000, cgroup_root=self.root)
        self.assertEqual(req.groups[0].traffic_class, "high")

    def test_rejects_cgroup_outside_current_user_hierarchy(self):
        system = os.path.join(self.root, "system.slice", "NetworkManager.service")
        os.makedirs(system)
        with self.assertRaises(oh.InvalidRequest):
            oh.parse_request(
                self.request([{"cgroup": system, "class": "high"}]),
                caller_uid=1000, cgroup_root=self.root)

    def test_rejects_unknown_keys_and_classes(self):
        for group in (
            {"cgroup": self.agent, "class": "unlimited"},
            {"cgroup": self.agent, "class": "high", "command": "sh"},
        ):
            with self.assertRaises(oh.InvalidRequest):
                oh.parse_request(self.request([group]), 1000, self.root)
```

- [ ] **Step 2: Run the helper tests and verify the module is missing**

Run: `python3 -m unittest nothing-widgets/test_optimizer_helper.py -v`

Expected: ERROR with `ModuleNotFoundError: No module named 'optimizer_helper'`.

- [ ] **Step 3: Add immutable request models and strict JSON parsing**

Start `optimizer_helper.py` with `#!/usr/bin/env python3` so the root-owned installed
copy is directly executable by the three exact sudoers commands.

```python
from dataclasses import dataclass
from typing import Tuple


TRAFFIC_CLASSES = frozenset(("high", "normal", "low"))


class InvalidRequest(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedGroup:
    cgroup: str
    cgroup_id: int
    traffic_class: str


@dataclass(frozen=True)
class NetworkRequest:
    version: int
    groups: Tuple[ValidatedGroup, ...]
```

Reject non-dicts, any top-level key outside `version` and `groups`, versions other
than integer `1`, more than 128 groups, duplicate cgroups, non-string fields, and
any group key outside `cgroup` and `class`.

- [ ] **Step 4: Validate canonical cgroup location and identity**

Resolve the supplied cgroup with `os.path.realpath`; require an existing directory
under:

```python
allowed = os.path.realpath(os.path.join(
    cgroup_root, "user.slice", "user-%d.slice" % uid,
))
```

Require `real == allowed or real.startswith(allowed + os.sep)`. Derive the cgroup
ID from `os.stat(real).st_ino`; never accept a numeric ID from stdin.

- [ ] **Step 5: Add caller and size-limit tests**

```python
def test_caller_uid_must_be_a_real_non_root_login_uid(self):
    with self.assertRaises(oh.InvalidRequest):
        oh.parse_request(self.request([]), caller_uid=0, cgroup_root=self.root)

def test_more_than_128_groups_is_rejected(self):
    groups = [{"cgroup": self.agent, "class": "high"}] * 129
    with self.assertRaises(oh.InvalidRequest):
        oh.parse_request(self.request(groups), 1000, self.root)
```

- [ ] **Step 6: Run validation tests**

Run: `python3 -m unittest nothing-widgets/test_optimizer_helper.py -v`

Expected: all request-validation cases pass.

- [ ] **Step 7: Commit the validation boundary**

```bash
git add nothing-widgets/optimizer_helper.py nothing-widgets/test_optimizer_helper.py
git diff --cached --check
git commit -m "Validate optimizer network policies"
```

---

### Task 2: Probe support and generate owned nftables/tc plans

**Files:**
- Modify: `nothing-widgets/optimizer_helper.py`
- Modify: `nothing-widgets/test_optimizer_helper.py`

**Interfaces:**
- Consumes: `NetworkRequest`, injected root command runner
- Produces: `NetworkEnvironment`, `CommandPlan`, `probe_environment(runner, uid)`, `build_plan(environment, request)`

- [ ] **Step 1: Write deterministic probe tests**

```python
class ProbeTest(unittest.TestCase):
    def test_default_route_selects_one_non_loopback_interface(self):
        runner = FakeRunner({
            ("ip", "-j", "route", "show", "default"): json.dumps([
                {"dst": "default", "dev": "wlp0s20f3", "metric": 600}
            ]),
            ("stat", "-fc", "%T", "/sys/fs/cgroup"): "cgroup2fs\n",
        })
        env = oh.probe_environment(runner, uid=1000)
        self.assertEqual(env.interface, "wlp0s20f3")

    def test_custom_root_qdisc_is_refused(self):
        runner = supported_runner(qdisc=[{"kind": "cake", "handle": "8001:", "root": True}])
        with self.assertRaises(oh.UnsupportedEnvironment):
            oh.probe_environment(runner, uid=1000)
```

Define `FakeRunner(outputs=None, fail=())` with `run(command, input_text="")` that
records command tuples and stdin, returns configured stdout, and returns failure for
configured exact commands. Define `supported_runner(qdisc=None, fail=())` with
`cgroup2fs`, one `wlp0s20f3` default route, nft check success, and either the supplied
qdisc JSON or `[{'kind': 'fq_codel', 'handle': '0:', 'root': True}]`.

- [ ] **Step 2: Run probe tests and verify missing APIs fail**

Run: `python3 -m unittest nothing-widgets.test_optimizer_helper.ProbeTest -v`

Expected: ERROR because `probe_environment` is not defined.

- [ ] **Step 3: Implement a read-only capability probe**

Add the environment and plan types:

```python
@dataclass(frozen=True)
class NetworkEnvironment:
    uid: int
    interface: str
    table: str
    original_qdisc: str


@dataclass(frozen=True)
class CommandPlan:
    apply: Tuple[Tuple[str, ...], ...]
    nft_rules: str
    restore: Tuple[Tuple[str, ...], ...]
```

Use argument-list commands:

```python
("stat", "-fc", "%T", "/sys/fs/cgroup")
("ip", "-j", "route", "show", "default")
("tc", "-j", "qdisc", "show", "dev", interface)
("nft", "--check", "--file", "-")
```

The nft check receives a temporary ruleset on stdin containing `meta cgroup` and
`meta priority set 1:1`; delete nothing because check mode performs no mutation.
Require cgroup2fs, exactly one best-metric default route, interface matching
`[A-Za-z0-9_.:-]{1,15}` and not `lo`, nft check success, and either no explicit
root qdisc or a kernel-default handle `0:`. Report JSON capability details without
raising from the CLI `probe` command.

- [ ] **Step 4: Write command-plan tests with all three traffic classes**

```python
def test_plan_uses_namespaced_table_and_owned_qdisc_handle(self):
    env = oh.NetworkEnvironment(
        uid=1000, interface="wlp0s20f3", table="nothing_opt_1000",
        original_qdisc="kernel-default")
    request = request_with_ids(high=501, normal=502, low=503)
    plan = oh.build_plan(env, request)
    self.assertIn(("tc", "qdisc", "replace", "dev", "wlp0s20f3",
                   "root", "handle", "1:", "prio", "bands", "3"), plan.apply)
    self.assertIn("meta cgroup 501 meta priority set 1:1", plan.nft_rules)
    self.assertIn("meta cgroup 502 meta priority set 1:2", plan.nft_rules)
    self.assertIn("meta cgroup 503 meta priority set 1:3", plan.nft_rules)
    self.assertNotIn("flush ruleset", plan.nft_rules)
```

Define `request_with_ids(high, normal, low)` as a `NetworkRequest(version=1,
groups=(ValidatedGroup('/cg/high', high, 'high'),
ValidatedGroup('/cg/normal', normal, 'normal'),
ValidatedGroup('/cg/low', low, 'low')))`. This helper bypasses path parsing only in
command-plan unit tests; validation tests continue through `parse_request`.

- [ ] **Step 5: Generate the exact owned plan**

The apply sequence is:

```text
tc qdisc replace dev "$interface" root handle 1: prio bands 3
tc qdisc add dev "$interface" parent 1:1 handle 10: fq_codel
tc qdisc add dev "$interface" parent 1:2 handle 20: fq_codel
tc qdisc add dev "$interface" parent 1:3 handle 30: fq_codel
nft --file -
```

The nft stdin document creates only `table inet nothing_opt_$uid`, one route/output
chain at mangle priority, and one rule per validated cgroup. The rule sets skb
priority to class `1:1`, `1:2`, or `1:3`; it does not set or overwrite firewall
marks used by VPNs. Escape no user strings because only validated integer cgroup IDs
and fixed identifiers enter the ruleset.

The restore plan deletes the namespaced nft table and root qdisc only when root-owned
state says they were created and a fresh probe still sees table name
`nothing_opt_$uid` and qdisc handle `1:` with kind `prio`.

- [ ] **Step 6: Run probe and plan tests**

Run: `python3 -m unittest nothing-widgets/test_optimizer_helper.py -v`

Expected: all cases pass; fake runners record commands but execute no network mutation.

- [ ] **Step 7: Commit probing and command planning**

```bash
git add nothing-widgets/optimizer_helper.py nothing-widgets/test_optimizer_helper.py
git diff --cached --check
git commit -m "Plan owned optimizer network rules"
```

---

### Task 3: Apply and restore network policy transactionally

**Files:**
- Modify: `nothing-widgets/optimizer_helper.py`
- Modify: `nothing-widgets/test_optimizer_helper.py`

**Interfaces:**
- Consumes: `NetworkEnvironment`, `CommandPlan`, `NetworkRequest`
- Produces: `HelperResult`, `apply_policy(runner, request, state_root, uid) -> HelperResult`, `restore_policy(runner, uid, state_root) -> HelperResult`, `sudo_uid(environment) -> int`, `emit(result, stdout, code=0) -> int`, `probe_result(uid) -> HelperResult`, `apply_from_json(raw, uid) -> HelperResult`, `restore_for_uid(uid) -> HelperResult`, `main(argv=None, stdin=None, stdout=None) -> int`

Add the result type before the transaction functions:

```python
@dataclass(frozen=True)
class HelperResult:
    ok: bool
    changed: bool
    stage: str
    detail: str
    active: bool = False
```

- [ ] **Step 1: Write transaction and rollback tests**

```python
class TransactionTest(unittest.TestCase):
    def test_nft_failure_removes_owned_qdisc(self):
        runner = supported_runner(fail={("nft", "--file", "-")})
        result = oh.apply_policy(runner, valid_request(), self.tmp.name, uid=1000)
        self.assertFalse(result.ok)
        self.assertIn(("tc", "qdisc", "del", "dev", "wlp0s20f3", "root"),
                      runner.commands)

    def test_restore_deletes_only_recorded_objects(self):
        write_root_state(self.tmp.name, uid=1000, interface="wlp0s20f3",
                         table="nothing_opt_1000", qdisc_handle="1:")
        result = oh.restore_policy(supported_runner(), 1000, self.tmp.name)
        self.assertTrue(result.ok)

    def test_restore_refuses_a_changed_qdisc(self):
        write_root_state(self.tmp.name, uid=1000, interface="wlp0s20f3",
                         table="nothing_opt_1000", qdisc_handle="1:")
        runner = supported_runner(qdisc=[{"kind": "cake", "handle": "1:", "root": True}])
        result = oh.restore_policy(runner, 1000, self.tmp.name)
        self.assertFalse(result.ok)
        self.assertNotIn(("tc", "qdisc", "del", "dev", "wlp0s20f3", "root"),
                         runner.commands)
```

Define `valid_request()` with the Task 2 `request_with_ids(501, 502, 503)` helper.
Define `write_root_state(state_root, uid, interface, table, qdisc_handle)` to create
`$state_root/$uid.json` containing version `1`, qdisc kind `prio`, and those exact
values. State-ownership behavior is tested separately by patching `os.stat` owner
fields; tests do not require root.

- [ ] **Step 2: Run transaction tests and verify missing functions fail**

Run: `python3 -m unittest nothing-widgets.test_optimizer_helper.TransactionTest -v`

Expected: ERROR because `apply_policy` and `restore_policy` are not defined.

- [ ] **Step 3: Implement root-owned state and transactional apply**

Use `/run/nothing-optimizer/$uid.json`, directory mode `0700`, file mode `0600`,
owned by root. Persist version, UID, interface, table, qdisc handle/kind, and creation
timestamp through temp-file fsync plus `os.replace`.

Before apply, restore a prior helper-owned policy for the same UID. Apply qdiscs,
then nft rules. After each step, record compensating actions. On failure, execute
compensations in reverse order and return JSON with `ok=false`, stage, and a bounded
error string. Write state only after a fresh post-apply probe sees the expected table
and qdisc.

- [ ] **Step 4: Implement idempotent restore**

No state file returns success with `changed=false`. With state, re-probe interface
and owned objects. Delete only the matching nft table and `prio` qdisc handle `1:`.
If ownership cannot be proven, retain the object and state file and return failure.
On complete restore, unlink only the exact root-owned state file.

- [ ] **Step 5: Implement the strict CLI**

```python
def main(argv=None, stdin=None, stdout=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv not in (["probe"], ["apply"], ["restore"]):
        return emit(HelperResult(False, False, "usage", "invalid command"), stdout, 2)
    uid = sudo_uid(os.environ)
    if argv == ["probe"]:
        return emit(probe_result(uid), stdout)
    if argv == ["apply"]:
        raw = (stdin or sys.stdin).read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            return emit(HelperResult(False, False, "validate", "request too large"), stdout, 2)
        return emit(apply_from_json(raw, uid), stdout)
    return emit(restore_for_uid(uid), stdout)
```

Reject missing, zero, nonnumeric, or mismatched `SUDO_UID`. Emit one JSON object and
no human text on stdout; operational diagnostics go in the bounded `detail` field.

- [ ] **Step 6: Add CLI contract tests**

Test exact command acceptance, 64-KiB stdin limit, invalid JSON, missing `SUDO_UID`,
probe JSON fields, and that `apply` never reaches the runner after validation fails.

- [ ] **Step 7: Run all helper tests**

Run: `python3 -W error::ResourceWarning nothing-widgets/test_optimizer_helper.py`

Expected: `OK` with no sudo or network mutation.

- [ ] **Step 8: Commit the transactional helper**

```bash
git add nothing-widgets/optimizer_helper.py nothing-widgets/test_optimizer_helper.py
git diff --cached --check
git commit -m "Apply optimizer network policy safely"
```

---

### Task 4: Connect the optional helper to the optimizer core and UI

**Files:**
- Modify: `nothing-widgets/optimizer_core.py`
- Modify: `nothing-widgets/test_optimizer_core.py`
- Modify: `nothing-widgets/nothing-widgets.py` in the Optimizer class only

**Interfaces:**
- Consumes: core `NetworkBackend`, `Capability`, `AppGroup`, `RolePolicy`; helper stdin/stdout contract
- Produces: `PrivilegedNetworkBackend.probe()`, `apply(groups, policies)`, `restore()` and verified `network` capability

- [ ] **Step 1: Write helper-client tests**

```python
class PrivilegedNetworkBackendTest(unittest.TestCase):
    def test_apply_sends_only_cgroup_and_fixed_class(self):
        runner = FakeRunner(json_outputs={
            ("sudo", "-n", oc.OPTIMIZER_HELPER, "apply"): {"ok": True, "active": True}
        })
        backend = oc.PrivilegedNetworkBackend(runner)
        cap = backend.apply(sample_groups(), sample_policy_map())
        payload = json.loads(runner.stdins[-1])
        self.assertEqual(set(payload), {"version", "groups"})
        self.assertEqual(set(payload["groups"][0]), {"cgroup", "class"})
        self.assertEqual(cap.level, "full")

    def test_failed_post_apply_stays_basic(self):
        runner = FakeRunner(json_outputs={
            ("sudo", "-n", oc.OPTIMIZER_HELPER, "apply"): {"ok": False, "active": False}
        })
        cap = oc.PrivilegedNetworkBackend(runner).apply(
            sample_groups(), sample_policy_map())
        self.assertEqual(cap.level, "fallback")
        self.assertIn("BASIC", cap.detail)
```

Extend the core-plan `FakeRunner` with `json_outputs` and a `stdins` list. Define
`sample_policy_map()` as `{group.key: oc.policy_for(oc.Mode.FOCUS, group.role,
True) for group in sample_groups()}` using the core plan's `sample_groups()` helper.

- [ ] **Step 2: Run client tests and verify missing backend fails**

Run: `python3 -m unittest nothing-widgets.test_optimizer_core.PrivilegedNetworkBackendTest -v`

Expected: ERROR because `PrivilegedNetworkBackend` is not defined.

- [ ] **Step 3: Implement the exact sudo client**

Use `/usr/local/libexec/nothing-optimizer-helper`. Run only:

```python
("sudo", "-n", OPTIMIZER_HELPER, "probe")
("sudo", "-n", OPTIMIZER_HELPER, "apply")
("sudo", "-n", OPTIMIZER_HELPER, "restore")
```

Build the request from managed groups with nonempty cgroups and network classes
`high`, `normal`, or `low`. Deduplicate cgroups by strongest class. Parse bounded
JSON stdout; any timeout, nonzero exit, malformed JSON, `ok=false`, or
`active=false` yields `Capability("network", "fallback", "NETWORK · BASIC")`.

- [ ] **Step 4: Wire network apply/restore into `ResourceController`**

Network failure is independent: keep successful CPU/I/O/power policy and replace
only the network capability. Restore invokes helper restore but never blocks other
restoration. Interface changes are handled inside the helper's apply transaction.

- [ ] **Step 5: Update the GTK capability label only**

The existing resource row reads the snapshot capability. Display
`NETWORK · PRIORITIZED` only for `level == "full"`; otherwise display
`NETWORK · BASIC`. Do not add another mode, switch, or settings page.

- [ ] **Step 6: Run core and helper suites**

Run: `python3 -W error::ResourceWarning nothing-widgets/test_optimizer_core.py && python3 -W error::ResourceWarning nothing-widgets/test_optimizer_helper.py`

Expected: both suites report `OK`.

- [ ] **Step 7: Commit the helper client and capability UI**

```bash
git add nothing-widgets/optimizer_core.py nothing-widgets/test_optimizer_core.py
git add -p nothing-widgets/nothing-widgets.py
git diff --cached --check
git commit -m "Connect optimizer network priority"
```

Inspect the staged GTK hunk; it may touch only the Optimizer network capability row.

---

### Task 5: Install, document, and verify the privileged helper

**Files:**
- Create: `config/nothing-optimizer.sudoers`
- Modify: `install.sh`
- Modify: `README.md`

**Interfaces:**
- Consumes: `optimizer_helper.py` and client path `/usr/local/libexec/nothing-optimizer-helper`
- Produces: opt-in `./install.sh --with-optimizer-sudo`, exact sudo commands, removal instructions

- [ ] **Step 1: Add exact sudoers entries**

```sudoers
arjun ALL=(root) NOPASSWD: /usr/local/libexec/nothing-optimizer-helper probe
arjun ALL=(root) NOPASSWD: /usr/local/libexec/nothing-optimizer-helper apply
arjun ALL=(root) NOPASSWD: /usr/local/libexec/nothing-optimizer-helper restore
```

Do not add wildcards or authorize the repo copy.

- [ ] **Step 2: Extend the installer with a distinct opt-in flag**

When any argument equals `--with-optimizer-sudo`, first run the helper test suite,
then install the helper with:

```bash
sudo install -m 0755 -o root -g root \
  "$R/nothing-widgets/optimizer_helper.py" \
  /usr/local/libexec/nothing-optimizer-helper
```

Create a temporary user-substituted sudoers file, validate it with
`sudo visudo -c -q -f "$tmp"`, install mode `0440` to
`/etc/sudoers.d/nothing-optimizer`, and delete the temporary file through the
existing trap/cleanup path. Existing `--with-cleaner-sudo` behavior remains usable.

- [ ] **Step 3: Document exact scope and removal**

Document that prioritization is egress-only and effective during local interface
contention. Describe `NETWORK · BASIC` versus `NETWORK · PRIORITIZED`, the refusal
of custom qdiscs, the opt-in command, and removal:

```bash
sudo /usr/local/libexec/nothing-optimizer-helper restore
sudo rm /etc/sudoers.d/nothing-optimizer
sudo rm /usr/local/libexec/nothing-optimizer-helper
```

- [ ] **Step 4: Validate syntax without installing**

Run: `bash -n install.sh`

Expected: exit 0.

Run: `python3 -m py_compile nothing-widgets/optimizer_helper.py nothing-widgets/optimizer_core.py`

Expected: exit 0.

Run: `python3 -W error::ResourceWarning nothing-widgets/test_optimizer_core.py && python3 -W error::ResourceWarning nothing-widgets/test_optimizer_helper.py`

Expected: both suites report `OK` without sudo.

- [ ] **Step 5: Validate generated sudoers in a temporary file**

Run the same `sed` substitution as the installer into a `mktemp` path and execute
`visudo -c -q -f "$optimizer_sudoers_tmp"`. This is a read-only syntax validation and must
not install the rule.

Expected: exit 0.

- [ ] **Step 6: Commit installer and documentation**

```bash
git add config/nothing-optimizer.sudoers install.sh README.md
git diff --cached --check
git commit -m "Install optimizer network helper"
```

- [ ] **Step 7: Offer, but do not assume, privileged live verification**

Ask for approval before running `./install.sh --with-optimizer-sudo`. If approved,
record the existing qdisc and nft table list, install, run helper `probe`, apply a
policy containing only disposable test cgroups, generate controlled upload
contention, confirm high-lane latency/throughput improvement, call helper `restore`,
and prove the original qdisc/table state is restored byte-for-byte where the tools
provide stable JSON.

If live installation is not approved, report unit, syntax, sudoers, and dry-run
verification as complete and label privileged network behavior as not live-tested.
