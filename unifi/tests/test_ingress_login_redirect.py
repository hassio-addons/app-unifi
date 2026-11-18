"""App-owned HTTPS redirect, never cookie/role bypass or arbitrary destination."""

from pathlib import Path
import shutil
import subprocess
import unittest

APP = Path(__file__).resolve().parents[1]


class IngressLoginRedirectTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "requires Node.js")
    def test_browser_redirect_matrix_and_no_query_destination(self):
        subprocess.run(
            [
                "node",
                str(APP / "tests/ingress_login_redirect_test.cjs"),
                str(APP / "rootfs/usr/share/unifi-os/ingress-login-redirect.js"),
            ],
            check=True,
            timeout=15,
        )
