"""The ingress prototype must stay gated and preserve the direct/vendor paths."""

import os
from pathlib import Path
import subprocess
import sys
import unittest
from uuid import uuid4

import yaml

APP = Path(__file__).resolve().parents[1]
CONFIG = APP / "rootfs/etc/unifi-os/ingress.conf"
UNIT = APP / "rootfs/etc/systemd/system/unifi-os-ingress.service"


class IngressBoundaryTest(unittest.TestCase):
    def test_ingress_always_on_but_never_host_published(self):
        manifest = yaml.safe_load((APP / "config.yaml").read_text())
        self.assertIs(manifest.get("ingress"), True)
        # The linter requires default ingress_port 8099 to be implicit.
        self.assertNotIn("ingress_port", manifest)
        self.assertEqual(manifest["ingress_entry"], "/ha/login_redirect/")
        # The only ingress option is the opt-in HTTP cookie compatibility; other
        # option defaults may exist, but no retired ingress toggle may return.
        self.assertIs(manifest["options"]["ingress_allow_insecure_http"], False)
        self.assertEqual(manifest["schema"]["ingress_allow_insecure_http"], "bool")
        for retired in ("enable_ingress", "ingress"):
            self.assertNotIn(retired, manifest["options"])
            self.assertNotIn(retired, manifest["schema"])
        self.assertNotIn("8099/tcp", manifest["ports"])
        # Open Web UI goes through ingress; a direct webui is rejected by the linter.
        self.assertNotIn("webui", manifest)
        dockerfile = (APP / "Dockerfile").read_text()
        self.assertIn("systemctl mask nginx.service", dockerfile)
        enable = dockerfile[dockerfile.index("systemctl enable") :]
        enable = enable[: enable.index("\n\n")]
        self.assertIn("unifi-os-ingress.service", enable)
        # Pulled in by ingress only; enabling it would make boot wait on it.
        self.assertNotIn("unifi-os-console-port.service", enable)
        self.assertIn("useradd --system --user-group", dockerfile)
        entrypoint = (APP / "rootfs/usr/local/bin/unifi-os-entrypoint").read_text()
        self.assertLess(
            entrypoint.index(
                "install -d -m 0755 -o unifi-port -g unifi-port /run/unifi-os-console-port"
            ),
            entrypoint.index("/usr/lib/systemd/systemd"),
        )
        unit = UNIT.read_text()
        self.assertNotIn("unifi-os-ingress-test-enabled", unit)
        self.assertNotIn("ConditionPathExists", unit)
        self.assertIn("ExecStartPre=+/usr/local/bin/unifi-os-ingress-options\n", unit)
        self.assertLess(
            unit.index("ExecStartPre=+/usr/local/bin/unifi-os-ingress-options"),
            unit.index("ExecStartPre=/usr/sbin/nginx -t"),
        )
        self.assertIn(
            "unifi-os-ingress.service",
            (APP / "rootfs/usr/local/bin/unifi-os-health").read_text(),
        )

    def test_only_supervisor_with_validated_prefix_and_fixed_upstream(self):
        text = CONFIG.read_text()
        self.assertIn("allow 172.30.32.2;", text)
        self.assertIn("deny all;", text)
        self.assertIn("~^/api/hassio_ingress/[A-Za-z0-9_-]+$", text)
        self.assertIn("proxy_pass https://127.0.0.1:11443;", text)
        self.assertNotIn("proxy_pass $", text)
        self.assertNotIn("real_ip_header", text)
        self.assertNotIn("proxy_set_header Authorization", text)
        self.assertNotIn("proxy_hide_header Content-Security-Policy", text)
        self.assertIn("js_content ingress_login.redirect;", text)
        self.assertIn('"http:0" 1;', text)
        unit = UNIT.read_text()
        self.assertIn("Wants=unifi-os-console-port.service", unit)
        # Never a start dependency: nginx must not wait on Supervisor.
        self.assertNotIn("After=unifi-os-console-port.service", unit)
        self.assertNotIn("unifi-os-setup-notification.service", unit)
        # Mandatory bind (no "-"): its stable source exists before systemd.
        self.assertIn(
            "BindReadOnlyPaths=/run/unifi-os-console-port:/run/unifi-os-ingress-port\n",
            unit,
        )
        self.assertIn(
            "/run/unifi-os-ingress-port/secure-port.json",
            (APP / "rootfs/usr/share/unifi-os/ingress-login.js").read_text(),
        )
        self.assertNotIn("/run/unifi-ha-api", unit)
        self.assertNotIn("LoadCredential", unit)
        publisher = (
            APP / "rootfs/etc/systemd/system/unifi-os-console-port.service"
        ).read_text()
        self.assertNotIn("Before=unifi-os-ingress.service", publisher)
        self.assertNotIn("unifi-os-bootstrap", publisher)
        self.assertIn("TimeoutStartSec=300", publisher)
        self.assertNotIn("RuntimeDirectory=", publisher)
        self.assertNotIn("\nDynamicUser=", publisher)
        self.assertNotIn("[Install]", publisher)
        self.assertIn("User=unifi-port\n", publisher)
        self.assertIn("ReadWritePaths=/run/unifi-os-console-port\n", publisher)

    def test_streaming_websocket_and_cookie_scope_without_auth_bypass(self):
        text = CONFIG.read_text()
        for directive in (
            "proxy_http_version 1.1;",
            "proxy_set_header Upgrade $http_upgrade;",
            "proxy_set_header Connection $ingress_connection;",
            "proxy_buffering off;",
            "proxy_request_buffering off;",
            "proxy_cookie_path ~^/(.*)$ $ingress_path/$1;",
            'add_header Cache-Control "no-store" always;',
            "access_log off;",
        ):
            self.assertIn(directive, text)
        unit = UNIT.read_text()
        for directive in (
            "DynamicUser=yes",
            "NoNewPrivileges=yes",
            "CapabilityBoundingSet=\n",
            "ProtectSystem=strict",
            "RuntimeDirectory=unifi-os-ingress",
        ):
            self.assertIn(directive, unit)

    def test_reload_validates_before_signalling_and_logs_errors(self):
        unit = UNIT.read_text()
        reloads = [
            line.split("=", 1)[1]
            for line in unit.splitlines()
            if line.startswith("ExecReload=")
        ]
        self.assertEqual(
            reloads,
            [
                "/usr/sbin/nginx -t -c /etc/unifi-os/ingress.conf",
                "/bin/kill -HUP $MAINPID",
            ],
        )
        text = CONFIG.read_text()
        self.assertIn("error_log stderr error;", text)
        self.assertIn("worker_connections 1024;", text)

    def test_units_use_profile_compatible_seccomp_hardening(self):
        def directives(path):
            values = {}
            for line in path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    values.setdefault(key, []).append(value)
            return values

        publisher = directives(
            APP / "rootfs/etc/systemd/system/unifi-os-console-port.service"
        )
        nginx = directives(UNIT)
        for unit in (nginx, publisher):
            for key, value in (
                ("SystemCallArchitectures", "native"),
                ("SystemCallFilter", "@system-service"),
                ("SystemCallErrorNumber", "EPERM"),
                ("LockPersonality", "yes"),
                ("RestrictRealtime", "yes"),
                ("RestrictSUIDSGID", "yes"),
                ("ProtectKernelLogs", "yes"),
                ("ProtectClock", "yes"),
                # The enforcing app profile and Docker's read-only /proc/sys.
                ("ProtectKernelTunables", "no"),
            ):
                self.assertEqual(unit.get(key), [value], key)
            # Its hostname self-bind conflicts with the app's AppArmor profile.
            self.assertNotIn("ProtectHostname", unit)
        self.assertEqual(nginx["UMask"], ["0077"])
        # The bridge's DynamicUser must read the published port file.
        self.assertEqual(publisher["UMask"], ["0022"])
        self.assertEqual(publisher["IPAddressAllow"], ["172.30.32.2 127.0.0.1"])
        self.assertEqual(publisher["IPAddressDeny"], ["any"])


@unittest.skipUnless(
    os.environ.get("UNIFI_INGRESS_TEST_IMAGE"), "requires Linux nginx/python test image"
)
class IngressRuntimeTest(unittest.TestCase):
    def test_real_nginx_with_synthetic_vendor_upstream(self):
        name = f"unifi-ingress-test-{uuid4().hex}"
        failure = None
        try:
            self.run_fixture(name)
        except BaseException as error:
            failure = error
            raise
        finally:
            # A timed-out Docker client does not necessarily remove its container.
            try:
                subprocess.run(
                    ["docker", "rm", "--force", name],
                    check=False,
                    timeout=20,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
                if failure is None:
                    raise
                print(
                    "Ingress cleanup failed; original test failure retained",
                    file=sys.stderr,
                )

    def run_fixture(self, name):
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--network=none",
                "--user=65534:65534",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--tmpfs=/run/unifi-os-ingress:mode=0700,uid=65534,gid=65534",
                "--tmpfs=/run/unifi-os-ingress-port:mode=0755,uid=65534,gid=65534",
                "--tmpfs=/tmp:mode=1777",
                "--mount",
                f"type=bind,src={CONFIG},dst=/fixture/ingress.conf,readonly",
                "--mount",
                f"type=bind,src={APP / 'tests/ingress_fixture.py'},dst=/fixture/test.py,readonly",
                "--mount",
                f"type=bind,src={APP / 'rootfs/usr/share/unifi-os/ingress-adapter.js'},dst=/usr/share/unifi-os/ingress-adapter.js,readonly",
                "--mount",
                f"type=bind,src={APP / 'rootfs/usr/share/unifi-os/ingress-cookies.js'},dst=/usr/share/unifi-os/ingress-cookies.js,readonly",
                # Use the image's baked login assets so this transport test also
                # fails if COPY rootfs omitted either module or browser script.
                "--entrypoint=python3",
                os.environ["UNIFI_INGRESS_TEST_IMAGE"],
                "/fixture/test.py",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
