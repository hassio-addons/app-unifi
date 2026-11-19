"""Ensure the rootfs adapter does not replace vendor units or runtime files."""

from pathlib import Path, PurePosixPath
import unittest

ROOTFS = Path(__file__).resolve().parents[1] / "rootfs"
UNITS = ROOTFS / "etc/systemd/system"
# Image symlinks that systemd resolves for namespace paths.
ALIASES = {
    "/lib/": "/usr/lib/",
    "/var/lib/uosserver/": "/var/lib/unifi-os-app/uos/state/",
}


def directives(path):
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")):
            continue
        key, _, value = line.partition("=")
        values.setdefault(key, []).append(value)
    return values


def words(values):
    return [word for value in values for word in value.split()]


def canonical(path):
    path = path.lstrip("-")
    for alias, target in ALIASES.items():
        if (path + "/").startswith(alias):
            path = target + path[len(alias) :]
    return PurePosixPath(path)


class VendorBoundaryTest(unittest.TestCase):
    def test_vendor_base_units_not_supplied(self):
        for name in ("uosserver.service", "uosserver-updater.service"):
            with self.subTest(unit=name):
                self.assertFalse((ROOTFS / "etc/systemd/system" / name).exists())
                self.assertTrue(
                    (
                        ROOTFS
                        / "etc/systemd/system"
                        / (name + ".d/home-assistant.conf")
                    ).is_file()
                )

    def test_vendor_executables_not_replaced(self):
        for name in ("updater-service", "uosserver-service", "discovery", "pasta"):
            with self.subTest(binary=name):
                self.assertFalse((ROOTFS / "var/lib/uosserver/bin" / name).exists())
        self.assertFalse((ROOTFS / "opt/unifi-os/installer").exists())

    def test_worker_executes_vendor_not_a_replacement(self):
        text = (ROOTFS / "usr/local/bin/unifi-os-worker").read_text()
        self.assertIn("exec /opt/unifi-os/installer --non-interactive", text)
        self.assertIn("exec /var/lib/uosserver/bin/uosserver-service", text)

    def test_updater_writable_paths_are_never_executable(self):
        updater = directives(UNITS / "uosserver-updater.service.d/home-assistant.conf")
        writable = words(updater["ReadWritePaths"])
        noexec = words(updater["NoExecPaths"])
        # systemd (verified with 259) applies ReadWritePaths=/NoExecPaths=/
        # ExecPaths= per entry: a broader NoExecPaths=/ does not reach a
        # separately listed writable path, and ExecPaths= would undo
        # ProtectSystem=. So every writable path is listed as non-executable.
        self.assertEqual(writable, noexec[: len(writable)])
        self.assertIn("/dev", noexec)
        self.assertNotIn("/", noexec)
        self.assertNotIn("ExecPaths", updater)
        self.assertEqual(updater["ProtectSystem"], ["strict"])
        self.assertEqual(updater["ProtectHome"], ["read-only"])
        # Writable via the strict table unless listed: /run/user is covered
        # by ProtectHome; Podman's runtime directory is listed explicitly.
        self.assertIn("/run/user/1001", writable)
        # Without the legacy layout the storage-root alias is hidden; with it,
        # a later-sorting runtime drop-in makes it writable and non-executable.
        self.assertEqual(
            updater["TemporaryFileSystem"],
            ["/usr/lib/unifi/data:ro,nosuid,nodev,noexec,mode=0755"],
        )
        self.assertNotIn("/lib/unifi/data", writable)
        entrypoint = (ROOTFS / "usr/local/bin/unifi-os-entrypoint").read_text()
        runtime = "uosserver-updater.service.d/legacy-layout.conf"
        self.assertIn(runtime, entrypoint)
        self.assertGreater("legacy-layout.conf", "home-assistant.conf")
        for line in ("'TemporaryFileSystem='", "'ReadWritePaths=/lib/unifi/data'", "'NoExecPaths=/lib/unifi/data'"):
            self.assertIn(line, entrypoint)
        for path in writable:
            self.assertNotEqual(canonical(path), PurePosixPath("/var/lib/unifi-os-app/uos/state/bin"))
        self.assertNotIn("CAP_SYS_ADMIN", " ".join(updater["CapabilityBoundingSet"]))
        self.assertIn("/run/systemd", words(updater["ReadOnlyPaths"]))

    def test_vendor_units_check_executables_and_hide_credentials(self):
        for unit, binary in (
            ("uosserver-updater.service", "updater-service"),
            ("uosserver.service", "uosserver-service"),
        ):
            values = directives(UNITS / (unit + ".d/home-assistant.conf"))
            with self.subTest(unit=unit):
                self.assertEqual(
                    values["ExecStartPre"][:2],
                    [
                        "+/usr/local/bin/unifi-os-state-ownership",
                        "+/usr/local/bin/unifi-os-exec-guard /var/lib/uosserver/bin/" + binary,
                    ],
                )
                hidden = words(values["InaccessiblePaths"])
                self.assertIn("-/run/unifi-ha-api", hidden)
                self.assertIn("-/run/credentials", hidden)
        worker = (ROOTFS / "usr/local/bin/unifi-os-worker").read_text()
        self.assertLess(
            worker.index("for path in /run/unifi-ha-api /run/credentials"),
            worker.index("exec /opt/unifi-os/installer"),
        )

    def test_log_forwarder_and_notifiers_have_no_capabilities(self):
        for unit in (
            "unifi-os-log.service",
            "unifi-os-alert-notification.service",
            "unifi-os-setup-notification.service",
            "unifi-os-setup-status.service",
            "unifi-os-port-alias@.service",
        ):
            values = directives(UNITS / unit)
            with self.subTest(unit=unit):
                self.assertEqual(values["CapabilityBoundingSet"], [""])
                self.assertEqual(values["NoNewPrivileges"], ["yes"])
                self.assertEqual(values["SystemCallArchitectures"], ["native"])
                if unit != "unifi-os-setup-status.service":
                    self.assertEqual(values["RestrictSUIDSGID"], ["yes"])
        log = directives(UNITS / "unifi-os-log.service")
        self.assertEqual(log["ProtectSystem"], ["strict"])
        self.assertEqual(log["RestrictAddressFamilies"], ["AF_UNIX"])

    def test_units_running_tar_or_podman_do_not_restrict_set_id_syscalls(self):
        # systemd's RestrictSUIDSGID= filter made GNU tar fail with ENOSYS.
        for unit in (
            "unifi-os-shutdown-checkpoint.service",
            "uosserver-updater.service.d/home-assistant.conf",
            "unifi-os-setup-status.service",
            "unifi.service",
            "unifi-os-bootstrap.service",
        ):
            with self.subTest(unit=unit):
                self.assertNotIn("RestrictSUIDSGID", directives(UNITS / unit))

    def test_layout_ordering_in_bootstrap_dropin(self):
        unit = (ROOTFS / "etc/systemd/system/unifi-os-bootstrap.service").read_text()
        override = (
            ROOTFS
            / "etc/systemd/system/unifi-os-bootstrap.service.d/legacy-layout.conf"
        ).read_text()
        self.assertNotIn("unifi.service", unit)
        self.assertIn("After=unifi.service", override)


if __name__ == "__main__":
    unittest.main()
