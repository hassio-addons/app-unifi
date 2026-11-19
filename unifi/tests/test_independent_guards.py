"""Independent Docker exec/hook launches must retain credential/startup guards."""

import os
from pathlib import Path
import unittest

from docker_fixture import run_container

APP = Path(__file__).resolve().parents[1]


class IndependentGuardTest(unittest.TestCase):
    def test_independently_invoked_adapters_strip_tokens_before_children(self):
        for name in (
            "health",
            "worker",
            "network-options",
            "prepare-mongo",
            "installation-guard",
            "state-ownership",
            "exec-guard",
            "alert",
            "fatal",
            "vendor-selfcheck",
        ):
            text = (APP / "rootfs/usr/local/bin" / ("unifi-os-" + name)).read_text()
            self.assertIn(
                "set -euo pipefail\nunset SUPERVISOR_TOKEN HASSIO_TOKEN\n", text
            )
        entry = (APP / "rootfs/usr/local/bin/unifi-os-entrypoint").read_text()
        self.assertLess(
            entry.index("unifi-os-installation-guard"),
            entry.index("/usr/lib/systemd/systemd"),
        )

    @unittest.skipUnless(
        os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
    )
    def test_independent_health_execution_does_not_pass_credentials_to_children(self):
        run_container(
            r"""
mkdir -p /run/stubs /run/user/1001
touch /run/user/1001/unifi-rootless-ready
cat > /run/stubs/cat <<'STUB'
#!/bin/bash
if [[ "$1" == /proc/self/attr/current ]]; then printf 'unifi (enforce)\n'; else /bin/cat "$@"; fi
STUB
cat > /run/stubs/systemctl <<'STUB'
#!/bin/bash
if [[ "$1" == show ]]; then printf '42\n'; fi
STUB
printf '#!/bin/bash\nprintf "1001\\n"\n' > /run/stubs/awk
printf '#!/bin/bash\nexit 0\n' > /run/stubs/curl
cat > /run/stubs/setpriv <<'STUB'
#!/bin/bash
test -z "${SUPERVISOR_TOKEN:-}" && test -z "${HASSIO_TOKEN:-}" || exit 88
touch /run/credential-free-child
STUB
chmod 755 /run/stubs/*
export PATH=/run/stubs:$PATH
/usr/local/bin/unifi-os-health
test -f /run/credential-free-child
""",
            timeout=30,
            docker_options=(
                "--env=SUPERVISOR_TOKEN=synthetic-supervisor-token",
                "--env=HASSIO_TOKEN=synthetic-hassio-token",
            ),
        )

    @unittest.skipUnless(
        os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
    )
    def test_partial_installation_or_drift_is_refused_before_systemd(self):
        run_container(
            r"""
root=/var/lib/unifi-os-app/uos
mkdir -p "$root/bootstrap" "$root/state" "$root/home"
/usr/local/bin/unifi-os-installation-guard
printf 'APP_VERSION=5.1.42\n' > "$root/state/server.conf"
if /usr/local/bin/unifi-os-installation-guard; then exit 1; fi
printf '5.1.42\n' > "$root/bootstrap/installed-version"
/usr/local/bin/unifi-os-installation-guard
printf 'APP_VERSION=5.1.43\n' > "$root/state/server.conf"
if /usr/local/bin/unifi-os-installation-guard; then exit 1; fi
printf '5.1.43\n' > "$root/bootstrap/installed-version"
if /usr/local/bin/unifi-os-installation-guard; then exit 1; fi
""",
            timeout=30,
        )
