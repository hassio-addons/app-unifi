"""Guard the enforcing profile: required rules present, and it parses."""

from pathlib import Path
import re
import shutil
import subprocess
import unittest

APP = Path(__file__).resolve().parents[1]
POLICY = APP / "apparmor.txt"
HASH = "[0-9a-f]" * 8

REQUIRED_RULES = (
    # Kernel controls, including bind-mount and fresh-mount aliases.
    "audit deny /{,**/}sysrq-trigger wklx,",
    "deny /proc/kcore rw,",
    "deny /{,**/}sys/kernel{,/**} wklx,",
    "deny /{,**/}sys/vm{,/**} wklx,",
    "audit deny /{,**/}power/state wklx,",
    "audit deny /{,**/}fs/binfmt_misc{,/**} wklx,",
    "audit deny mount /{,**/}sys/{kernel,vm}{,/**} -> /**,",
    "audit deny /sys/kernel/security{,/**} wklx,",
    "deny /sys/module{,/**} wklx,",
    "deny /sys/firmware{,/**} rwklx,",
    "audit deny mount fstype=sysfs -> /{,**/}{[^s]*,s,s[^y]*,sy,sy[^s]*,sys?*}{,/},",
    # Kernel-wide filesystem types nothing in the app mounts.
    *(
        f"audit deny mount fstype={fstype},"
        for fstype in (
            "binfmt_misc",
            "debugfs",
            "tracefs",
            "bpf",
            "configfs",
            "efivarfs",
            "pstore",
            "cgroup",
        )
    ),
    # Peers and profile transitions pinned to this app's own profile names.
    "ptrace peer=unifi,",
    "ptrace peer=local_unifi,",
    f"ptrace peer={HASH}_unifi,",
    "change_profile -> unifi,",
    "change_profile -> local_unifi,",
    f"change_profile -> {HASH}_unifi,",
)


def rules():
    return [
        line.strip()
        for line in POLICY.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


class AppArmorProfileTest(unittest.TestCase):
    def test_required_rules_are_present(self):
        present = set(rules())
        for rule in REQUIRED_RULES:
            with self.subTest(rule=rule):
                self.assertIn(rule, present)

    def test_no_unrestricted_ptrace_or_profile_change(self):
        for rule in rules():
            with self.subTest(rule=rule):
                self.assertNotEqual(rule, "ptrace,")
                self.assertFalse(rule.startswith("change_profile -> *"))
                self.assertNotIn("unconfined", rule)

    def test_sysrq_and_sys_alias_denials_are_retained(self):
        text = POLICY.read_text()
        self.assertRegex(
            text,
            r"audit deny mount /\{,\*\*/\}sysrq-trigger -> /\{,\*\*/\}\{\[\^s\]\*,.*sysrq-trigger\?\*\},",
        )
        self.assertRegex(text, r"audit deny mount /\{,\*\*/\}sys\{,/\} -> .*\{,/\},")

    def test_securityfs_and_sysfs_remount_exceptions_are_exactly_the_needed_ones(self):
        text = POLICY.read_text()
        securityfs = re.search(
            r"audit deny mount fstype=securityfs -> /\{(.*)\},", text
        )
        self.assertIsNotNone(securityfs)
        alternatives = securityfs.group(1).split(",")
        # The worker's read-only mount target is the only allowed one.
        self.assertNotIn("sys/kernel/security/", alternatives)
        self.assertIn("sys/kernel/security/?**", alternatives)
        worker = (APP / "rootfs/usr/local/bin/unifi-os-worker").read_text()
        self.assertIn(
            "mount -t securityfs -o ro,nosuid,nodev,noexec securityfs /sys/kernel/security",
            worker,
        )
        remount = re.search(
            r"audit deny remount options in \(([^)]*)\) /sys/\{(.*)\},", text
        )
        self.assertIsNotNone(remount)
        self.assertNotIn("ro", [flag.strip() for flag in remount.group(1).split(",")])
        self.assertNotIn("fs/cgroup", remount.group(2).split(","))
        entrypoint = (APP / "rootfs/usr/local/bin/unifi-os-entrypoint").read_text()
        self.assertIn("mount -o remount,rw /sys/fs/cgroup", entrypoint)

    def test_entrypoint_and_health_accept_exactly_the_profile_names(self):
        pattern = r"^([0-9a-f]{8}_|local_)?unifi\ \(enforce\)$"
        for name in ("unifi-os-entrypoint", "unifi-os-health"):
            text = (APP / "rootfs/usr/local/bin" / name).read_text()
            with self.subTest(script=name):
                self.assertIn(f'[[ "${{profile}}" =~ {pattern} ]]', text)
        accepted = re.compile(pattern.replace("\\ ", " "))
        for label in (
            "unifi (enforce)",
            "local_unifi (enforce)",
            "a0d7b954_unifi (enforce)",
        ):
            self.assertRegex(label, accepted)
        for label in (
            "unifi (complain)",
            "docker-default (enforce)",
            "other_unifi (enforce)",
            "a0d7b954_unifi_x (enforce)",
            "unconfined",
        ):
            self.assertNotRegex(label, accepted)

    def test_logind_uses_container_uts_without_weakening_kernel_alias_denial(self):
        override = (
            APP
            / "rootfs/etc/systemd/system/systemd-logind.service.d/home-assistant.conf"
        ).read_text()
        self.assertIn("ProtectHostname=no", override)
        self.assertNotIn("ProtectKernel", override)

    @unittest.skipUnless(shutil.which("apparmor_parser"), "apparmor_parser unavailable")
    def test_profile_parses(self):
        # -Q: do not load into the kernel; -K: do not write the cache.
        result = subprocess.run(
            ["apparmor_parser", "-QK", str(POLICY)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
