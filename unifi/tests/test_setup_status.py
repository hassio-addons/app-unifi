"""Narrow native status projection: strict bool, no config disclosure or writes."""

import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

APP = Path(__file__).resolve().parents[1]


class SetupStatusTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "setup_status", APP / "rootfs/usr/share/unifi-os/setup-status.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_inner_program_projects_only_vendor_top_level_boolean(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.yaml"
            for flag in ("true", "false"):
                path.write_text(
                    "owner: private-user\nisSetup: " + flag + "\nsecret: never-print\n"
                )
                with mock.patch("pathlib.Path", return_value=path):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        exec(self.module.INNER, {})
                self.assertEqual(output.getvalue(), flag + "\n")

    def test_ambiguous_missing_nested_or_non_boolean_setting_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.yaml"
            for content in (
                "owner: private-user\n",
                "  isSetup: true\n",
                'isSetup: "true"\n',
                "isSetup: 1\n",
                "isSetup: true\nisSetup: false\n",
                "isSetup: true\nisSetup: unknown\n",
            ):
                path.write_text(content)
                with (
                    self.subTest(content=content),
                    mock.patch("pathlib.Path", return_value=path),
                    self.assertRaises(AssertionError),
                ):
                    exec(self.module.INNER, {})

    def test_probe_discards_failed_command_output(self):
        for result in (
            mock.Mock(returncode=1, stdout=b"secret", stderr=b"secret"),
            mock.Mock(returncode=0, stdout=b"false\nsecret", stderr=b""),
        ):
            with (
                mock.patch.object(self.module.Path, "read_text", return_value="5.1.42"),
                mock.patch.object(
                    self.module.subprocess, "run", return_value=result
                ) as run,
            ):
                self.assertIsNone(self.module.probe())
            self.assertEqual(run.call_args.kwargs["timeout"], 15)
            self.assertIn("/usr/bin/python3", run.call_args.args[0])

    def test_supported_version_has_a_single_value(self):
        spec = importlib.util.spec_from_file_location(
            "setup_progress", APP / "rootfs/usr/share/unifi-os/setup-progress-screen.py"
        )
        progress = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(progress)
        self.assertEqual(self.module.SUPPORTED_VERSION, progress.SUPPORTED_VERSION)
        self.assertIn(
            f'echo "{self.module.SUPPORTED_VERSION}" > /opt/unifi-os/version',
            (APP / "Dockerfile").read_text(),
        )

    def test_unsupported_version_warns_once_and_never_probes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "version").write_text("9.9.9\n")
            errors = io.StringIO()
            with (
                mock.patch.object(self.module, "VERSION", root / "version"),
                mock.patch.object(self.module, "WARNED", root / "warned"),
                mock.patch.object(self.module.subprocess, "run") as run,
                contextlib.redirect_stderr(errors),
            ):
                self.assertIsNone(self.module.probe())
                self.assertIsNone(self.module.probe())
            run.assert_not_called()
            self.assertEqual(errors.getvalue().count("WARNING"), 1)
            self.assertTrue(errors.getvalue().startswith("<4>"))
            self.assertIn("9.9.9", errors.getvalue())

    def test_explicit_completion_is_recorded_and_stops_sampling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for flag, recorded in ((None, False), (False, False), (True, True)):
                with (
                    self.subTest(flag=flag),
                    mock.patch.object(self.module, "STATUS", root / "status.json"),
                    mock.patch.object(self.module, "COMPLETE", root / "complete"),
                    mock.patch.object(self.module, "probe", return_value=flag),
                ):
                    self.module.main()
                    self.assertEqual((root / "complete").is_file(), recorded)
        marker = "ConditionPathExists=!" + str(self.module.COMPLETE)
        for unit in ("unifi-os-setup-status.service", "unifi-os-setup-status.timer"):
            with self.subTest(unit=unit):
                self.assertIn(
                    marker, (APP / "rootfs/etc/systemd/system" / unit).read_text()
                )
        entrypoint = (APP / "rootfs/usr/local/bin/unifi-os-entrypoint").read_text()
        self.assertIn(
            "install -d -m 0755 -o uosserver -g uosserver "
            + str(self.module.COMPLETE.parent),
            entrypoint,
        )

    def test_timer_is_transient_not_a_migration_completion_receipt(self):
        unit = (
            APP / "rootfs/etc/systemd/system/unifi-os-setup-status.timer"
        ).read_text()
        self.assertIn("OnUnitInactiveSec=30", unit)
        self.assertNotIn("Persistent=true", unit)
        self.assertNotIn("After=unifi-os-bootstrap.service", unit)
        self.assertEqual(
            str(self.module.STATUS), "/run/unifi-os-setup-status/status.json"
        )
