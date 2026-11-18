"""Keep the candidate on the original app identity with explicit release gates."""

from pathlib import Path
import re
import unittest

import yaml

APP = Path(__file__).resolve().parents[1]


class CandidateManifestTest(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load((APP / "config.yaml").read_text())

    def test_original_identity_and_manual_breaking_upgrade(self):
        self.assertEqual(self.config["slug"], "unifi")
        self.assertNotIn("stage", self.config)
        self.assertEqual(self.config["version"], "dev")
        self.assertIn("6.0.0", self.config["breaking_versions"])
        self.assertFalse((APP.parent / "unifi-os-test/config.yaml").exists())

    def test_private_storage_and_cold_backups(self):
        data = next(m for m in self.config["map"] if m["type"] == "data")
        self.assertEqual(data["path"], "/lib/unifi/data")
        self.assertFalse(data["read_only"])
        # Supervisor ignores pre/post hooks for cold backups; none may be set.
        self.assertEqual(self.config["backup"], "cold")
        self.assertNotIn("backup_pre", self.config)
        self.assertNotIn("backup_post", self.config)
        backup = next(m for m in self.config["map"] if m["type"] == "backup")
        self.assertTrue(backup["read_only"])
        self.assertEqual(self.config["backup_exclude"], ["*/uos/home", "*/uos/state"])

    def test_ingress_streams_large_uploads(self):
        # Supervisor otherwise buffers whole request bodies (e.g. .unf restores).
        self.assertIs(self.config["ingress"], True)
        self.assertIs(self.config["ingress_stream"], True)

    def test_every_base_image_is_pinned_by_digest(self):
        # The app linter only accepts plain image names in build.yaml, so the
        # Dockerfile's FROM lines carry the digest pins instead.
        dockerfile = (APP / "Dockerfile").read_text()
        bases = [line.split()[1] for line in dockerfile.splitlines() if line.startswith("FROM ")]
        self.assertEqual(len(bases), 3)
        for base in bases:
            self.assertRegex(base, r"^[a-z0-9./-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")

    def test_direct_console_and_default_legacy_host_ports(self):
        self.assertNotIn("webui", self.config)
        self.assertEqual(self.config["panel_icon"], "mdi:lan")
        for port, host in {
            "8443/tcp": 8443,
            "8843/tcp": 8843,
            "10001/udp": 10001,
            "8080/tcp": 8080,
            # Vendor ports mapped directly alongside the legacy aliases.
            "11443/tcp": 11443,
            "8444/tcp": 8444,
            "10003/udp": 10003,
        }.items():
            self.assertEqual(self.config["ports"][port], host)
        for legacy in ("8443/tcp", "8843/tcp", "10001/udp"):
            self.assertTrue(
                self.config["ports_description"][legacy].startswith("Legacy ")
            )
        original_keys = {
            "161/udp",
            "1900/udp",
            "3478/udp",
            "5514/udp",
            "6789/tcp",
            "8080/tcp",
            "8443/tcp",
            "8843/tcp",
            "8880/tcp",
            "10001/udp",
        }
        self.assertTrue(original_keys.issubset(self.config["ports"]))
        self.assertEqual(
            set(self.config["ports"]), set(self.config["ports_description"])
        )

    def test_options_retained_without_extra_host_access(self):
        self.assertTrue(self.config["homeassistant_api"])
        self.assertEqual(
            set(self.config["schema"]),
            {"log_level", "memory_max", "memory_init", "ingress_allow_insecure_http"},
        )
        self.assertEqual(self.config["options"], {"ingress_allow_insecure_http": False})
        self.assertEqual(self.config["schema"]["ingress_allow_insecure_http"], "bool")
        for flag in (
            "host_pid",
            "host_network",
            "host_dbus",
            "docker_api",
            "hassio_api",
        ):
            self.assertFalse(self.config.get(flag, False))

    def test_translated_network_schema_matches_ports_and_aliases(self):
        for path in sorted((APP / "translations").glob("*.yaml")):
            with self.subTest(language=path.stem):
                network = yaml.safe_load(path.read_text())["network"]
                self.assertEqual(set(network), set(self.config["ports"]))
                for key, target in {
                    "8443/tcp": "11443/TCP",
                    "8843/tcp": "8444/TCP",
                    "10001/udp": "10003/UDP",
                }.items():
                    self.assertIn(target, network[key])

    def test_checkpoint_restore_precedes_capability_drop_and_writer_start(self):
        entrypoint = (APP / "rootfs/usr/local/bin/unifi-os-entrypoint").read_text()
        restore = entrypoint.index("/usr/local/bin/unifi-os-runtime-storage restore")
        invalidate = entrypoint.index(
            "/usr/local/bin/unifi-os-runtime-storage invalidate"
        )
        drop = entrypoint.index("exec setpriv --bounding-set=-mknod")
        self.assertLess(restore, invalidate)
        self.assertLess(invalidate, drop)
        self.assertLess(drop, entrypoint.index("/usr/lib/systemd/systemd"))

    def test_shutdown_checkpoint_reverses_order_without_stopping_updater_in_server(
        self,
    ):
        unit = (
            APP / "rootfs/etc/systemd/system/unifi-os-shutdown-checkpoint.service"
        ).read_text()
        self.assertIn("Before=uosserver.service uosserver-updater.service", unit)
        self.assertIn("After=user@1001.service", unit)
        self.assertIn("Requires=user@1001.service", unit)
        self.assertIn("TimeoutStopSec=150", unit)
        self.assertIn("RemainAfterExit=yes", unit)
        self.assertIn("WantedBy=multi-user.target", unit)
        self.assertIn(
            "unifi-os-shutdown-checkpoint.service", (APP / "Dockerfile").read_text()
        )
        self.assertEqual(self.config["timeout"], 300)
        cleanup = (APP / "rootfs/usr/local/bin/unifi-os-storage-cleanup").read_text()
        self.assertNotIn("systemctl stop", cleanup)

    def test_no_legacy_vendor_patching_entrypoint(self):
        self.assertEqual(list((APP / "rootfs/etc/s6-overlay").glob("**/run")), [])
        dockerfile = (APP / "Dockerfile").read_text()
        self.assertIn('ENTRYPOINT ["/usr/local/bin/unifi-os-entrypoint"]', dockerfile)
        self.assertNotIn("verify_bytes", dockerfile)
        self.assertNotIn("webrtc_jni", dockerfile)

    def test_bootstrap_and_health_grace_cover_all_mongo_readiness_windows(self):
        bootstrap = (
            APP / "rootfs/etc/systemd/system/unifi-os-bootstrap.service"
        ).read_text()
        timeout = int(re.search(r"^TimeoutStartSec=(\d+)$", bootstrap, re.M)[1])
        prepare = (APP / "rootfs/usr/local/bin/unifi-os-prepare-mongo").read_text()
        readiness = int(re.search(r"deadline=\$\(\(SECONDS \+ (\d+)\)\)", prepare)[1])
        runtimes = re.search(r'case "\$\{version\}" in ([0-9.|]+)\)', prepare)[1].split("|")
        self.assertEqual(runtimes, ["3.6", "4.0", "4.2", "4.4"])
        # Each runtime may need a full readiness window; the vendor installer
        # (image load and first start) is budgeted 15 minutes on slow hosts.
        self.assertGreaterEqual(timeout, len(runtimes) * readiness + 15 * 60)
        period = re.search(r"--start-period=(\d+)m", (APP / "Dockerfile").read_text())
        self.assertGreaterEqual(int(period[1]) * 60, timeout)
