"""App alerts: fixed IDs, no secrets, delivered once, start failures withdrawn."""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from docker_fixture import run_container

APP = Path(__file__).resolve().parents[1]
UNITS = APP / "rootfs/etc/systemd/system"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AlertNotificationTest(unittest.TestCase):
    def setUp(self):
        self.module = load(
            "alert_notification",
            APP / "rootfs/usr/share/unifi-os/alert-notification.py",
        )
        self.helpers = load(
            "setup_notification",
            APP / "rootfs/usr/share/unifi-os/setup-notification.py",
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.alerts = Path(self.tmp.name) / "alerts"
        self.alerts.mkdir()
        self.state = Path(self.tmp.name) / "delivered.json"
        self.calls = []

    def request(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/addons/self/info"):
            return {"result": "ok", "data": {"slug": "a0d7b954_unifi"}}
        return []

    def deliver(self, state=True):
        with mock.patch.object(self.helpers, "request_json", side_effect=self.request):
            return self.module.deliver(
                self.helpers,
                "synthetic-token",
                self.alerts,
                self.state if state else None,
            )

    def payloads(self, suffix):
        return [k["payload"] for url, k in self.calls if url.endswith(suffix)]

    def test_alert_files_become_named_notifications_once(self):
        (self.alerts / "os_version_drift").write_text("Title line\nMessage body.\n")
        (self.alerts / ".alert.tmp123").write_text("ignored\nignored\n")
        (self.alerts / "Bad-ID").write_text("ignored\nignored\n")
        self.assertTrue(self.deliver())
        created = self.payloads("/persistent_notification/create")
        self.assertEqual(
            created,
            [
                {
                    "notification_id": "unifi_os_os_version_drift_a0d7b954_unifi",
                    "title": "UniFi OS Server: Title line",
                    "message": "Message body.",
                }
            ],
        )
        for url, kwargs in self.calls:
            self.assertTrue(url.startswith("http://supervisor/"))
            self.assertEqual(kwargs["token"], "synthetic-token")
        # A later trigger does not re-show what was already delivered...
        self.calls.clear()
        self.assertTrue(self.deliver())
        self.assertEqual(self.calls, [])
        # ...but a changed alert is delivered again.
        (self.alerts / "os_version_drift").write_text("Title line\nChanged.\n")
        self.assertTrue(self.deliver())
        self.assertEqual(len(self.payloads("/persistent_notification/create")), 1)

    def test_successful_start_withdraws_only_start_failure_alerts(self):
        (self.alerts / "checkpoint_incomplete").write_text("Backup\nMake one.\n")
        (self.alerts / "startup-complete").write_text("")
        self.assertTrue(self.deliver())
        dismissed = {p["notification_id"] for p in self.payloads("/dismiss")}
        self.assertIn("unifi_os_bootstrap_failed_a0d7b954_unifi", dismissed)
        self.assertIn("unifi_os_os_version_drift_a0d7b954_unifi", dismissed)
        self.assertNotIn("unifi_os_checkpoint_incomplete_a0d7b954_unifi", dismissed)
        self.calls.clear()
        self.assertTrue(self.deliver())
        self.assertEqual(self.calls, [])

    def test_direct_delivery_without_state_sends_each_alert_once(self):
        (self.alerts / "startup_refused").write_text("Refused\nBecause.\n")
        self.assertTrue(self.deliver(state=False))
        self.assertEqual(len(self.payloads("/persistent_notification/create")), 1)

    def test_api_failure_is_bounded_and_silent(self):
        (self.alerts / "startup_refused").write_text("Refused\nBecause.\n")
        with (
            mock.patch.object(
                self.helpers, "request_json", side_effect=OSError("secret body")
            ),
            mock.patch.object(self.module.time, "sleep") as sleep,
        ):
            self.assertFalse(
                self.module.deliver(self.helpers, "synthetic-token", self.alerts, None)
            )
        self.assertEqual(sleep.call_count, self.module.ATTEMPTS - 1)

    def test_oversized_or_incomplete_alerts_are_ignored(self):
        (self.alerts / "huge").write_text("Title\n" + "x" * 9000)
        (self.alerts / "untitled").write_text("only a title\n")
        self.assertEqual(self.module.read_alerts(self.alerts), {})

    def test_fatal_alert_ids_are_known_to_the_notifier(self):
        scripts = APP / "rootfs/usr/local/bin"
        raised = set()
        for path in scripts.glob("unifi-os-*"):
            text = path.read_text()
            raised.update(
                word
                for word in text.replace("\\\n", " ").split()
                if word in self.module.STARTUP_ALERTS
            )
        # Every start-failure ID the notifier withdraws is raised somewhere.
        self.assertEqual(raised, set(self.module.STARTUP_ALERTS))

    def test_units_deliver_unprivileged_on_changes_and_at_boot(self):
        service = (UNITS / "unifi-os-alert-notification.service").read_text()
        for line in (
            "DynamicUser=yes",
            "CapabilityBoundingSet=",
            "LoadCredential=ha-api-token:/run/unifi-ha-api/token",
            "ConditionPathExists=/run/unifi-ha-api/token",
            "WantedBy=multi-user.target",
        ):
            self.assertIn(line + "\n", service)
        path = (UNITS / "unifi-os-alerts.path").read_text()
        self.assertIn("PathChanged=/run/unifi-os-alerts\n", path)
        self.assertIn("Unit=unifi-os-alert-notification.service\n", path)
        bootstrap = (UNITS / "unifi-os-bootstrap.service").read_text()
        self.assertIn("OnFailure=unifi-os-fatal.service\n", bootstrap)
        dockerfile = (APP / "Dockerfile").read_text()
        self.assertIn(
            "unifi-os-alert-notification.service unifi-os-alerts.path", dockerfile
        )
        fatal = (APP / "rootfs/usr/local/bin/unifi-os-fatal").read_text()
        self.assertLess(
            fatal.index("systemctl start unifi-os-alert-notification.service"),
            fatal.index("systemctl --no-block exit 1"),
        )
        # The forwarder may be stopped before it catches up: the fatal path
        # writes the failing step's messages to the app log itself.
        self.assertLess(
            fatal.index("tee /run/unifi-os-log.pipe"),
            fatal.index("systemctl --no-block exit 1"),
        )

    def test_log_forwards_every_app_unit_by_exact_name(self):
        text = (APP / "rootfs/usr/local/bin/unifi-os-log").read_text()
        units = {path.name for path in UNITS.glob("*.service")} - {
            "unifi-os-port-alias@.service"
        }
        units |= {f"unifi-os-port-alias@{port}.service" for port in (8443, 8843, 10001)}
        units |= {
            "uosserver.service",
            "uosserver-updater.service",
            "init.scope",
        }
        units -= {"unifi-os-log.service"}
        for unit in sorted(units):
            with self.subTest(unit=unit):
                self.assertIn("\n    " + unit + "\n", text)
        self.assertNotIn("*", text.split("units=(", 1)[1])
        self.assertIn('--priority="${priority}"', text)
        # Too chatty (one line per podman call); its failures arrive via PID 1.
        self.assertNotIn("user@1001.service\n", text)
        service = (UNITS / "unifi-os-log.service").read_text()
        self.assertIn("ExecStartPre=+/usr/bin/systemctl log-target journal\n", service)
        self.assertIn("ExecStartPre=+/usr/bin/systemctl log-level notice\n", service)


@unittest.skipUnless(
    os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
)
class AlertProducerTest(unittest.TestCase):
    def test_helper_writes_atomic_plain_text_and_rejects_bad_ids(self):
        run_container(
            r"""
alert=/usr/local/bin/unifi-os-alert
$alert some_id "Title" "Message" 2>/dev/null   # no directory yet: no-op
mkdir -m 0755 /run/unifi-os-alerts
$alert some_id "Title" "Message two" 2>/dev/null
test "$(cat /run/unifi-os-alerts/some_id)" = "$(printf 'Title\nMessage two')"
test "$(stat -c %a /run/unifi-os-alerts/some_id)" = 644
for id in ../escape Bad x/y ''; do
    if $alert "$id" T M 2>/dev/null; then exit 1; fi
done
$alert --startup-complete
test -f /run/unifi-os-alerts/startup-complete
test "$(find /run/unifi-os-alerts -name '.alert.*' | wc -l)" = 0
""",
            timeout=30,
        )

    def test_installation_guard_explains_version_drift(self):
        run_container(
            r"""
root=/var/lib/unifi-os-app/uos
mkdir -p "$root/bootstrap" "$root/state" /run/unifi-os-alerts
printf '5.1.42\n' > "$root/bootstrap/installed-version"
printf 'APP_VERSION=5.2.9\n' > "$root/state/server.conf"
if /usr/local/bin/unifi-os-installation-guard 2>/run/err; then exit 1; fi
grep -q 'installation guard refused: vendor version drift' /run/err
grep -q "reports version '5.2.9'" /run/unifi-os-alerts/os_version_drift
grep -q 'only delivered with app updates' /run/unifi-os-alerts/os_version_drift
grep -q 'changed no data' /run/unifi-os-alerts/os_version_drift
""",
            timeout=30,
        )

    def test_failed_start_is_never_checkpointed_and_storage_is_not_created(self):
        run_container(
            r"""
mkdir -p /run/stubs /var/lib/unifi-os-app/uos/home /var/lib/unifi-os-app/uos/state
printf 'state\n' > /var/lib/unifi-os-app/uos/state/server.conf
cat > /run/stubs/systemctl <<'STUB'
#!/bin/bash
case "$*" in
    show*) printf 'inactive\n' ;;
    is-system-running) printf 'stopping\n' ;;
esac
STUB
printf '#!/bin/bash\ntouch /run/worker-called\n' > /run/stubs/unshare
chmod 755 /run/stubs/*
export PATH=/run/stubs:$PATH
# Not started: no Podman initialisation, checkpoint stays invalid.
/usr/local/bin/unifi-os-shutdown-checkpoint
test ! -e /run/worker-called
test -f /var/lib/unifi-os-app/uos/runtime-checkpoint.incomplete
test ! -e /var/lib/unifi-os-app/uos/runtime-checkpoint.tar.gz
# Started, but reported fatal: same.
mkdir -p /run/unifi-os-app
touch /run/unifi-os-app/started /run/unifi-os-app/fatal
/usr/local/bin/unifi-os-shutdown-checkpoint
test ! -e /var/lib/unifi-os-app/uos/runtime-checkpoint.tar.gz
# A completed start is checkpointed.
rm /run/unifi-os-app/fatal
/usr/local/bin/unifi-os-shutdown-checkpoint
test -f /run/worker-called
test -f /var/lib/unifi-os-app/uos/runtime-checkpoint.tar.gz
test ! -e /var/lib/unifi-os-app/uos/runtime-checkpoint.incomplete
""",
            timeout=60,
        )

    def test_bootstrap_failures_raise_specific_alerts_and_keep_status(self):
        run_container(
            r"""
mkdir -p /run/unifi-os-alerts /var/lib/unifi-os-app/unifi/data
touch /var/lib/unifi-os-app/unifi/data/system.properties
printf '#!/bin/bash\nexit 73\n' > /usr/local/bin/unifi-os-legacy-layout
if /usr/local/bin/unifi-os-bootstrap 2>/dev/null; then exit 1; else status=$?; fi
test "$status" = 73
grep -q 'nothing was imported or changed' /run/unifi-os-alerts/legacy_layout_failed
rm -r /var/lib/unifi-os-app/unifi /run/unifi-os-alerts/*
printf '#!/bin/bash\nexit 5\n' > /usr/local/bin/unifi-os-prepare-mongo
if /usr/local/bin/unifi-os-bootstrap 2>/dev/null; then exit 1; else status=$?; fi
test "$status" = 5
test -f /run/unifi-os-alerts/mongo_preparation_failed
test ! -e /run/unifi-os-app/started
""",
            timeout=30,
        )
