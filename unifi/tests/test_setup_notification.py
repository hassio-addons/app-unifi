"""Status-only setup notice: no import, auth bypass, token logging or receipts."""

import importlib.util
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

APP = Path(__file__).resolve().parents[1]


class SetupNotificationTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "setup_notification",
            APP / "rootfs/usr/share/unifi-os/setup-notification.py",
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_only_explicit_vendor_boolean_identifies_setup(self):
        self.assertIs(self.module.setup_needed({"isSetup": False}), True)
        self.assertIs(self.module.setup_needed({"isSetup": True}), False)
        for value in (
            {},
            None,
            [],
            {"isSetup": "false"},
            {"isSetup": 0},
            {"isSetup": None},
        ):
            with self.subTest(value=value):
                self.assertIsNone(self.module.setup_needed(value))

    def test_legacy_setup_creates_one_named_notification_without_migration(self):
        calls = []

        def request(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/api/system"):
                self.assertNotIn("token", kwargs)
                return {"isSetup": False}
            if url.endswith("/addons/self/info"):
                return {"result": "ok", "data": {"slug": "a0d7b954_unifi"}}
            if url.endswith("/has-legacy-net"):
                self.assertNotIn("token", kwargs)
                return {"hasLegacyNet": True}
            self.assertTrue(url.endswith("/persistent_notification/create"))
            payload = kwargs["payload"]
            self.assertEqual(
                payload["notification_id"], "unifi_os_setup_a0d7b954_unifi"
            )
            self.assertIn("migration setup needed", payload["title"])
            self.assertIn("Nothing has been imported automatically", payload["message"])
            return []

        with mock.patch.object(self.module, "request_json", side_effect=request):
            self.assertEqual(self.module.report("synthetic-token"), "notified")
        self.assertEqual(sum("payload" in args for _, args in calls), 1)
        self.assertFalse(any("legacy-net-migration" in url for url, _ in calls))
        self.assertFalse(any("setup/start" in url for url, _ in calls))

    def test_recorded_completion_dismisses_without_querying_the_vendor(self):
        with tempfile.TemporaryDirectory() as directory:
            complete = Path(directory) / "complete"
            complete.write_text("")
            with (
                mock.patch.object(self.module, "SETUP_COMPLETE", complete),
                mock.patch.object(
                    self.module,
                    "request_json",
                    side_effect=[
                        {"result": "ok", "data": {"slug": "a0d7b954_unifi"}},
                        [],
                    ],
                ) as request,
            ):
                self.assertEqual(self.module.report("synthetic-token"), "configured")
        urls = [call.args[0] for call in request.call_args_list]
        self.assertFalse(any(url.startswith(self.module.VENDOR) for url in urls))
        self.assertTrue(urls[-1].endswith("/persistent_notification/dismiss"))

    def test_missing_flag_does_not_touch_any_notification_or_identity(self):
        with mock.patch.object(self.module, "request_json", return_value={}) as request:
            self.assertEqual(self.module.report("synthetic-token"), "unknown")
        self.assertEqual(request.call_count, 1)

    def test_explicit_setup_complete_dismisses_only_this_apps_notice(self):
        with mock.patch.object(
            self.module,
            "request_json",
            side_effect=[
                {"isSetup": True},
                {"result": "ok", "data": {"slug": "a0d7b954_unifi"}},
                [],
            ],
        ) as request:
            self.assertEqual(self.module.report("synthetic-token"), "configured")
        url = request.call_args.args[0]
        self.assertTrue(url.endswith("/persistent_notification/dismiss"))
        self.assertEqual(
            request.call_args.kwargs["payload"],
            {"notification_id": "unifi_os_setup_a0d7b954_unifi"},
        )

    def test_unavailable_legacy_detection_uses_generic_setup_notice(self):
        with mock.patch.object(
            self.module,
            "request_json",
            side_effect=[
                {"isSetup": False},
                {"result": "ok", "data": {"slug": "a0d7b954_unifi"}},
                OSError("unavailable"),
                [],
            ],
        ) as request:
            self.assertEqual(self.module.report("synthetic-token"), "notified")
        self.assertEqual(
            request.call_args.kwargs["payload"]["title"], "UniFi OS setup needed"
        )

    def test_identity_cannot_inject_links_or_notification_ids(self):
        for slug in ("../other", "other?token=secret", "<script>"):
            with self.subTest(slug=slug), self.assertRaises(ValueError):
                self.module.notification(slug, True)

    def test_web_ui_link_is_token_free_and_uses_the_actual_ingress_mode(self):
        slug = "a0d7b954_unifi"
        notice = self.module.notification(slug, True, ingress=True)
        self.assertIn(f"[Open UniFi Web UI](/app/{slug})", notice["message"])
        self.assertNotIn(f"](/{slug})", notice["message"])
        self.assertNotIn("/hassio/ingress/", notice["message"])
        for value in (False, None, "true", 1):
            notice = self.module.notification(slug, False, ingress=value)
            self.assertIn(f"/config/app/{slug}/info", notice["message"])
            self.assertNotIn("/hassio/ingress/", notice["message"])
        self.assertIn("Local-account legacy import is not supported", notice["message"])
        self.assertIn(".unf", notice["message"])
        self.assertNotIn("/api/hassio_ingress/", notice["message"])
        self.assertNotIn("192.168.", notice["message"])
        self.assertNotIn("11441", notice["message"])

    def test_secure_console_port_metadata_honors_published_custom_bindings_only(self):
        self.assertEqual(
            self.module.console_port({"network": {"8443/tcp": 11441}}), 11441
        )
        self.assertEqual(
            self.module.console_port({"network": {"8443/tcp": 8443}}), 8443
        )
        # Defaults on both: the vendor-port mapping wins over the legacy alias.
        both_default = {"network": {"11443/tcp": 11443, "8443/tcp": 8443}}
        self.assertEqual(self.module.console_port(both_default), 11443)
        # A customized binding (the user's firewall intent) wins over a default.
        for network, expected in (
            ({"11443/tcp": 11443, "8443/tcp": 11441}, 11441),
            ({"11443/tcp": 11443, "8443/tcp": 9443}, 9443),
            ({"11443/tcp": 18443, "8443/tcp": 8443}, 18443),
            ({"11443/tcp": 18443, "8443/tcp": 9443}, 18443),
            ({"11443/tcp": None, "8443/tcp": 9443}, 9443),
            ({"11443/tcp": None, "8443/tcp": 8443}, 8443),
        ):
            with self.subTest(network=network):
                self.assertEqual(
                    self.module.console_port({"network": network}), expected
                )
        for value in (None, False, "11441", 0, 65536):
            self.assertEqual(
                self.module.console_port({"network": {"8443/tcp": value}}), 11443
            )
        # An explicit all-off map means native; a missing map is no answer.
        self.assertEqual(
            self.module.console_port(
                {"network": {"11443/tcp": None, "8443/tcp": None}}
            ),
            11443,
        )
        for info in ({}, {"network": None}, {"network": []}):
            with self.subTest(info=info):
                self.assertIsNone(self.module.console_port(info))
        with tempfile.TemporaryDirectory() as directory:
            port = Path(directory) / "secure-port.json"
            with mock.patch.object(self.module, "SECURE_PORT", port):
                self.module.publish_console_port(
                    {
                        "network": {"8443/tcp": 11441},
                        "options": {"secret": "never-write"},
                    }
                )
            self.assertEqual(json.loads(port.read_text()), {"port": 11441})
            self.assertEqual(port.stat().st_mode & 0o777, 0o644)

    def test_credential_is_transient_and_removed_before_vendor_start(self):
        entry = (APP / "rootfs/usr/local/bin/unifi-os-entrypoint").read_text()
        self.assertLess(
            entry.index("unset SUPERVISOR_TOKEN HASSIO_TOKEN"),
            entry.index("unifi-os-runtime-storage restore"),
        )
        self.assertIn("export -n notification_token", entry)
        self.assertIn("umask 077; printf '%s'", entry)
        self.assertIn("unset notification_token", entry)
        unit = (
            APP / "rootfs/etc/systemd/system/unifi-os-setup-notification.service"
        ).read_text()
        self.assertIn("LoadCredential=ha-api-token:/run/unifi-ha-api/token", unit)
        self.assertIn("DynamicUser=yes", unit)
        self.assertIn("CapabilityBoundingSet=\n", unit)
        self.assertIn("Type=exec", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertNotIn("RuntimeMaxSec=960", unit)
        self.assertNotIn("Type=oneshot", unit)
        self.assertIn("After=network.target unifi-os-bootstrap.service", unit)
        self.assertNotIn("Requires=uosserver", unit)
        self.assertNotIn("RuntimeDirectory=", unit)
        publisher = (
            APP / "rootfs/etc/systemd/system/unifi-os-console-port.service"
        ).read_text()
        self.assertIn("LoadCredential=ha-api-token:/run/unifi-ha-api/token", publisher)
        self.assertIn("User=unifi-port\n", publisher)
        self.assertIn("NoNewPrivileges=yes", publisher)
        self.assertIn("CapabilityBoundingSet=\n", publisher)
        self.assertIn("--publish-console-port", publisher)

    def test_console_port_refresh_is_independent_of_vendor_setup_status(self):
        with (
            mock.patch.object(
                self.module,
                "request_json",
                return_value={
                    "result": "ok",
                    "data": {"network": {"8443/tcp": 11441}},
                },
            ) as request,
            mock.patch.object(
                self.module, "publish_console_port", return_value=True
            ) as publish,
        ):
            self.assertTrue(self.module.refresh_console_port("synthetic-token"))
        request.assert_called_once_with(
            self.module.SUPERVISOR + "/addons/self/info", token="synthetic-token"
        )
        publish.assert_called_once_with({"network": {"8443/tcp": 11441}})

    def test_missing_network_map_publishes_nothing_so_the_bridge_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            port = Path(directory) / "secure-port.json"
            with (
                mock.patch.object(self.module, "SECURE_PORT", port),
                mock.patch.object(
                    self.module,
                    "request_json",
                    return_value={"result": "ok", "data": {"slug": "unifi"}},
                ),
            ):
                with self.assertRaises(ValueError):
                    self.module.refresh_console_port("synthetic-token")
            self.assertFalse(port.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_notification_does_not_use_working_data_or_complete_setup(self):
        text = (APP / "rootfs/usr/share/unifi-os/setup-notification.py").read_text()
        # The app's own completion marker is allowed; vendor working data is not.
        self.assertNotIn("/var/lib/unifi/", text)
        self.assertNotIn("/uos/home", text)
        self.assertNotIn("/uos/state", text)
        self.assertNotIn("/api/setup/start", text)
        self.assertNotIn("legacy-net-migration", text)
        self.assertNotIn("SUPERVISOR_TOKEN", text)

    def test_redirects_do_not_forward_credentials(self):
        self.assertIsNone(
            self.module.NoRedirect().redirect_request(
                None, None, 302, "Found", {}, "https://external.invalid"
            )
        )

    def test_native_observation_requires_explicit_fresh_boolean(self):
        with tempfile.TemporaryDirectory() as directory:
            status = Path(directory) / "status.json"
            with (
                mock.patch.object(self.module, "STATUS", status),
                mock.patch.object(self.module.time, "monotonic", return_value=100),
            ):
                for flag, expected in (
                    (False, True),
                    (True, False),
                    (None, None),
                    (1, None),
                ):
                    status.write_text(json.dumps({"isSetup": flag, "sampled_at": 95}))
                    self.assertIs(self.module.native_setup_needed(), expected)
                for stamp in (
                    -1,
                    101,
                    "95",
                    None,
                    float("nan"),
                    float("inf"),
                    10**1000,
                ):
                    status.write_text(
                        json.dumps({"isSetup": True, "sampled_at": stamp})
                    )
                    self.assertIsNone(self.module.native_setup_needed())
                status.write_text("not json")
                self.assertIsNone(self.module.native_setup_needed())

    def test_public_omission_dismisses_only_on_explicit_native_completion(self):
        with (
            mock.patch.object(self.module, "native_setup_needed", return_value=False),
            mock.patch.object(
                self.module,
                "request_json",
                side_effect=[
                    {},
                    {"result": "ok", "data": {"slug": "a0d7b954_unifi"}},
                    [],
                ],
            ) as request,
        ):
            self.assertEqual(self.module.report("synthetic-token"), "configured")
        self.assertTrue(
            request.call_args.args[0].endswith("/persistent_notification/dismiss")
        )
        self.assertEqual(
            request.call_args.kwargs["payload"]["notification_id"],
            "unifi_os_setup_a0d7b954_unifi",
        )

    def test_unchanged_needed_status_does_not_recreate_a_delivered_notice(self):
        with mock.patch.object(
            self.module, "request_json", return_value={"isSetup": False}
        ) as request:
            self.assertEqual(
                self.module.report("synthetic-token", notified=True), "notified"
            )
        self.assertEqual(request.call_count, 1)

    def test_monitor_watches_until_explicit_completion_and_logs_delivery_once(self):
        with (
            mock.patch.object(
                self.module, "refresh_console_port", return_value=True
            ) as port,
            mock.patch.object(
                self.module.Path, "read_text", return_value="synthetic-token"
            ),
            mock.patch.dict(
                self.module.os.environ, {"CREDENTIALS_DIRECTORY": "/synthetic"}
            ),
            mock.patch.object(self.module.time, "sleep"),
            mock.patch.object(self.module.time, "monotonic", return_value=0),
            mock.patch.object(
                self.module,
                "report",
                side_effect=["notified", "unknown", "notified", "configured"],
            ) as report,
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(self.module.main(), 0)
        self.assertEqual(output.getvalue().count("delivered to Home Assistant"), 1)
        # The port is owned by its own early publisher, not the notifier.
        port.assert_not_called()
        self.assertIn("notification dismissed", output.getvalue())
        self.assertEqual(
            [call.kwargs["notified"] for call in report.call_args_list],
            [False, True, True, True],
        )

    def test_probe_has_no_ha_credential_or_host_capability(self):
        unit = (
            APP / "rootfs/etc/systemd/system/unifi-os-setup-status.service"
        ).read_text()
        self.assertIn("User=uosserver", unit)
        self.assertIn("NoNewPrivileges=yes", unit)
        self.assertIn("CapabilityBoundingSet=\n", unit)
        self.assertNotIn("LoadCredential", unit)
        self.assertIn("TimeoutStartSec=20", unit)

    def test_unknown_observations_have_a_bounded_retry_budget(self):
        with (
            mock.patch.object(
                self.module, "refresh_console_port", return_value=True
            ) as port,
            mock.patch.object(
                self.module.Path, "read_text", return_value="synthetic-token"
            ),
            mock.patch.dict(
                self.module.os.environ, {"CREDENTIALS_DIRECTORY": "/synthetic"}
            ),
            mock.patch.object(self.module.time, "sleep") as sleep,
            mock.patch.object(self.module.time, "monotonic", side_effect=[0, 901]),
            mock.patch.object(
                self.module, "report", side_effect=OSError("private-error-never-log")
            ),
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(self.module.main(), 1)
        self.assertNotIn("private-error", output.getvalue())
        port.assert_not_called()
        self.assertNotIn("synthetic-token", output.getvalue())
        sleep.assert_called_once_with(self.module.RETRY_SECONDS)

    def test_port_publisher_is_bounded_and_independent_of_setup_status(self):
        environment = {"CREDENTIALS_DIRECTORY": "/synthetic"}
        for outcomes, expected, sleeps in (
            ([True], 0, 0),
            ([OSError("private-error"), ValueError("x"), True], 0, 2),
            ([OSError("private-error")] * 20, 1, 19),
        ):
            with (
                self.subTest(expected=expected, sleeps=sleeps),
                mock.patch.object(
                    self.module, "refresh_console_port", side_effect=outcomes
                ) as port,
                mock.patch.object(self.module, "report") as report,
                mock.patch.object(
                    self.module.Path, "read_text", return_value="synthetic-token"
                ),
                mock.patch.dict(self.module.os.environ, environment),
                mock.patch.object(self.module.time, "sleep") as sleep,
            ):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(self.module.publish_main(), expected)
            self.assertEqual(port.call_count, len(outcomes))
            self.assertEqual(sleep.call_count, sleeps)
            report.assert_not_called()
            self.assertNotIn("private-error", output.getvalue())
            self.assertNotIn("synthetic-token", output.getvalue())
