"""Non-fatal vendor self-check: pinned ingress literals and supported version."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

from docker_fixture import run_container

APP = Path(__file__).resolve().parents[1]
SCRIPT = APP / "rootfs/usr/local/bin/unifi-os-vendor-selfcheck"
INNER = APP / "rootfs/usr/share/unifi-os/vendor-selfcheck.py"
PORTAL = "/usr/share/unifi-core/app/node_modules/@ubnt/unifi-portal/dist"


def literals():
    block = SCRIPT.read_text().split("literals=(", 1)[1].split("\n)", 1)[0]
    return shlex.split(block)


class VendorSelfCheckTest(unittest.TestCase):
    def test_literals_are_exactly_the_ingress_rewrite_patterns(self):
        ingress = (APP / "rootfs/etc/unifi-os/ingress.conf").read_text()
        patterns = re.findall(r"^\s*sub_filter '((?:[^'\\]|\\.)*)'", ingress, re.M)
        for literal in literals():
            with self.subTest(literal=literal):
                self.assertIn(literal, patterns)
        self.assertEqual(len(literals()), 3)

    def run_inner(self, files):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "dist"
            root.mkdir()
            for name, body in files.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(body)
            program = INNER.read_text().replace(PORTAL, str(root))
            self.assertNotEqual(program, INNER.read_text())
            result = subprocess.run(
                [sys.executable, "-", *literals()],
                input=program,
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(result.stdout)

    def test_inner_check_reports_only_booleans(self):
        first, second, third = literals()
        result = self.run_inner(
            {"a.js": "x;" + first + ";secret", "chunks/b.js": third, "c.css": second}
        )
        self.assertEqual(result, {"bundles": True, "found": [True, False, True]})

    def test_check_never_fails_startup(self):
        options = (APP / "rootfs/usr/local/bin/unifi-os-network-options").read_text()
        self.assertIn(
            "/usr/local/bin/unifi-os-vendor-selfcheck \\\n"
            '            || echo "WARNING: vendor compatibility self-check did not complete" >&2',
            options,
        )
        self.assertIn("exit 0\n", SCRIPT.read_text().split("missing=()", 1)[0])


@unittest.skipUnless(
    os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
)
class VendorSelfCheckContainerTest(unittest.TestCase):
    def test_mismatches_warn_and_alert_without_failing(self):
        run_container(
            r"""
mkdir -p /run/stubs /run/unifi-os-alerts /var/lib/unifi-os-app/uos/state
printf 'APP_VERSION=5.1.99\n' > /var/lib/uosserver/server.conf
cat > /run/stubs/setpriv <<'STUB'
#!/bin/bash
printf '{"bundles": true, "found": [true, false, true]}\n'
STUB
chmod 755 /run/stubs/setpriv
PATH=/run/stubs:$PATH /usr/local/bin/unifi-os-vendor-selfcheck 2>/run/err
grep -q 'not the supported 5.1.42' /run/err
grep -q 'missing: cookie-path' /run/err
grep -q 'direct HTTPS console' /run/unifi-os-alerts/ingress_rewrite_mismatch
grep -q '5.1.99' /run/unifi-os-alerts/vendor_version_unsupported
# An unreachable runtime is reported, not fatal.
printf '#!/bin/bash\nexit 125\n' > /run/stubs/setpriv
printf 'APP_VERSION=5.1.42\n' > /var/lib/uosserver/server.conf
rm /run/unifi-os-alerts/*
PATH=/run/stubs:$PATH /usr/local/bin/unifi-os-vendor-selfcheck 2>/run/err
grep -q 'check skipped' /run/err
test -z "$(ls /run/unifi-os-alerts)"
""",
            timeout=30,
        )
