"""Run the browser API regression fixture, without an HA login or live devices."""

from pathlib import Path
import shutil
import subprocess
import unittest

APP = Path(__file__).resolve().parents[1]


class IngressBrowserAdapterTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "requires Node.js preference fixture")
    def test_preference_cookie_security_requires_http_opt_in(self):
        subprocess.run(
            [
                "node",
                str(APP / "tests/ingress_preference_test.cjs"),
                str(APP / "rootfs/usr/share/unifi-os/ingress-adapter.js"),
            ],
            check=True,
            timeout=30,
        )

    @unittest.skipUnless(shutil.which("node"), "requires Node.js cookie fixture")
    def test_cookie_compatibility_is_opt_in_and_scoped(self):
        subprocess.run(
            [
                "node",
                str(APP / "tests/ingress_cookies_test.cjs"),
                str(APP / "rootfs/usr/share/unifi-os/ingress-cookies.js"),
            ],
            check=True,
            timeout=30,
        )

    @unittest.skipUnless(shutil.which("node"), "requires Node.js browser API fixture")
    def test_login_history_and_dynamic_requests_stay_scoped(self):
        subprocess.run(
            [
                "node",
                str(APP / "tests/ingress_adapter_test.cjs"),
                str(APP / "rootfs/usr/share/unifi-os/ingress-adapter.js"),
            ],
            check=True,
            timeout=30,
        )
