"""Heap/log adapters must preserve vendor defaults and never execute options."""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

from docker_fixture import run_container

APP = Path(__file__).resolve().parents[1]


class NetworkOptionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        spec = importlib.util.spec_from_file_location(
            "network_options",
            APP / "rootfs/usr/share/unifi-os/apply-network-options.py",
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.module.ENV = self.root / "data/network.env"
        self.module.DROPIN = self.root / "unit/90-home-assistant-options.conf"
        self.module.BUS = self.root / "bus/90-home-assistant.conf"
        self.vendor = self.root / "vendor.env"
        self.user = self.root / "user.env"
        self.module.DEFAULTS = (self.vendor, self.user)
        self.original = 'UNIFI_JVM_OPTS="-Dunifi-os.server=true -XX:+UseG1GC -XX:MaxRAMPercentage=70.0 -Xss1024K"\nUNIFI_MQ_ENABLED=true\n'
        self.vendor.write_text(self.original)

    def test_heap_override_is_separate_idempotent_and_reversible(self):
        self.assertTrue(self.module.apply("512", "128"))
        env = self.module.ENV.read_text()
        self.assertIn("-Dunifi-os.server=true", env)
        self.assertIn("-XX:+UseG1GC", env)
        self.assertIn("-Xmx512m -Xms128m", env)
        self.assertIn(
            "EnvironmentFile=/data/unifi-ha/network.env", self.module.DROPIN.read_text()
        )
        self.assertFalse(self.module.apply("512", "128"))
        self.assertTrue(self.module.apply("", ""))
        self.assertFalse(self.module.ENV.exists())
        self.assertFalse(self.module.DROPIN.exists())
        self.assertEqual(self.vendor.read_text(), self.original)

    def test_existing_user_jvm_flags_are_not_lost(self):
        user = 'UNIFI_JVM_OPTS="-Duser.flag=retained -Xmx256M -Xms64M"\n'
        self.user.write_text(user)
        self.module.apply("512", "128")
        self.assertIn("-Duser.flag=retained", self.module.ENV.read_text())
        self.assertNotIn("-Xmx256M", self.module.ENV.read_text())
        self.assertEqual(self.user.read_text(), user)

    def test_one_heap_option_cannot_conflict_with_retained_bounds(self):
        self.user.write_text('UNIFI_JVM_OPTS="-Xmx256M -Xms64M"\n')
        with self.assertRaises(ValueError):
            self.module.apply("", "512")
        with self.assertRaises(ValueError):
            self.module.apply("32", "")
        self.assertFalse(self.module.ENV.exists())

    def test_invalid_values_and_non_owned_files_fail_closed(self):
        for values in (("0", ""), ("128", "512"), ("512; touch /evil", "")):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.module.apply(*values)
        self.module.ENV.parent.mkdir()
        self.module.ENV.write_text("User-owned file\n")
        with self.assertRaises(RuntimeError):
            self.module.apply("512", "128")
        self.assertEqual(self.module.ENV.read_text(), "User-owned file\n")

    def test_unsupported_environment_syntax_does_not_discard_user_settings(self):
        self.user.write_text("UNIFI_JVM_OPTS='custom JVM flags'\n")
        with self.assertRaises(RuntimeError):
            self.module.apply("512", "128")
        self.assertFalse(self.module.ENV.exists())

    def test_private_bus_override_is_owned_and_idempotent(self):
        text = (APP / "rootfs/usr/share/unifi-os/private-bus.conf").read_text()
        self.assertTrue(self.module.apply_private_bus(text))
        self.assertFalse(self.module.apply_private_bus(text))
        self.assertIn('<apparmor mode="disabled"/>', self.module.BUS.read_text())
        self.module.BUS.write_text("User-owned bus policy\n")
        with self.assertRaises(RuntimeError):
            self.module.apply_private_bus(text)
        self.assertEqual(self.module.BUS.read_text(), "User-owned bus policy\n")


@unittest.skipUnless(
    os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
)
class OptionsRenderTest(unittest.TestCase):
    def test_valid_options_and_safe_logging_mapping(self):
        run_container(
            r"""
printf '{"memory_max":512,"memory_init":128,"log_level":"warning"}' > /run/input.json
/usr/local/bin/unifi-os-options /run/input.json /run/output.json
jq -e '.memory_max == 512 and .memory_init == 128 and .priority == 4' /run/output.json
test "$(stat -c %a /run/output.json)" = 600
/usr/local/bin/unifi-os-options /run/missing.json /run/default.json
jq -e '.memory_max == null and .memory_init == null and .priority == 6 and .ingress_allow_insecure_http == false' /run/default.json
""",
            timeout=30,
        )

    def test_invalid_json_and_options_are_rejected_without_outputs(self):
        run_container(
            r"""
for json in '[]' '{' '{"memory_max":0}' '{"memory_max":128,"memory_init":512}' '{"memory_init":-1}' '{"memory_max":"512; touch /run/evil"}' '{"log_level":"bad"}' '{"ingress_allow_insecure_http":"true"}' '{"ingress_allow_insecure_http":1}' '{"ingress_allow_insecure_http":null}'; do
    printf '%s' "$json" > /run/input.json
    if /usr/local/bin/unifi-os-options /run/input.json /run/output.json; then exit 1; fi
    test ! -e /run/output.json
    test ! -e /run/evil
done
""",
            timeout=30,
        )

    def test_ingress_policy_is_boolean_only_and_readable_without_secrets(self):
        run_container(
            r"""
for flag in false true; do
    printf '{"ingress_allow_insecure_http":%s,"memory_max":512}' "$flag" > /run/input.json
    /usr/local/bin/unifi-os-options /run/input.json /run/output.json
    /usr/local/bin/unifi-os-ingress-options /run/output.json /run/policy.conf
    test "$(stat -c %a /run/policy.conf)" = 644
    expected=0; if [[ "$flag" == true ]]; then expected=1; fi
    grep -Fq "default ${expected};" /run/policy.conf
    test "$(wc -l < /run/policy.conf)" = 1
    ! grep -q memory /run/policy.conf
done
printf '{"ingress_allow_insecure_http":"true"}' > /run/invalid.json
if /usr/local/bin/unifi-os-ingress-options /run/invalid.json /run/rejected.conf; then exit 1; fi
test ! -e /run/rejected.conf
""",
            timeout=30,
        )


@unittest.skipUnless(
    os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
)
class NetworkOptionsActivationTest(unittest.TestCase):
    def test_inner_services_restart_only_after_a_change_and_checks_never_block(self):
        run_container(
            r"""
mkdir -p /run/stubs /run/user/1001
touch /run/user/1001/unifi-rootless-ready
printf '{"memory_max":null,"memory_init":null}' > /run/unifi-os-options.json
cat > /run/stubs/setpriv <<'STUB'
#!/bin/bash
printf '%s\n' "$*" >> /run/calls
# Heap/bus application: "python3 - MAX INIT BUS" prints the change report.
if [[ "$*" == *"/usr/bin/python3 - "*"<!--"* ]]; then cat /run/changed; fi
STUB
chmod 755 /run/stubs/setpriv
printf '#!/bin/bash\necho selfcheck >> /run/calls\nexit 1\n' > /usr/local/bin/unifi-os-vendor-selfcheck
printf '#!/bin/bash\necho cleanup >> /run/calls\nexit 1\n' > /usr/local/bin/unifi-os-storage-cleanup
export PATH=/run/stubs:$PATH
printf '{"network_changed": false, "bus_changed": false}' > /run/changed
/usr/local/bin/unifi-os-network-options 2>/dev/null
! grep -q 'restart dbus.service' /run/calls
! grep -q daemon-reload /run/calls
grep -qx selfcheck /run/calls
grep -qx cleanup /run/calls
grep -q 'setup-progress\|/usr/bin/python3 - 5.1.42' /run/calls
rm /run/calls
printf '{"network_changed": false, "bus_changed": true}' > /run/changed
/usr/local/bin/unifi-os-network-options 2>/dev/null
grep -q 'systemctl daemon-reload' /run/calls
grep -q 'systemctl restart dbus.service' /run/calls
grep -q 'systemctl try-restart unifi.service' /run/calls
""",
            timeout=60,
        )
