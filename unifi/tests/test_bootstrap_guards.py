"""Opt-in fail-closed tests in disposable, network-free Linux containers."""

import os
from pathlib import Path
import unittest

from docker_fixture import run_container

APP = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
)
class BootstrapGuardsTest(unittest.TestCase):
    def run_container(self, script, *arguments):
        run_container(script, *arguments, timeout=30)

    def test_failed_layout_blocks_preparation_and_vendor_start(self):
        self.run_container(r"""
mkdir -p /var/lib/unifi-os-app/unifi/data
touch /var/lib/unifi-os-app/unifi/data/system.properties
printf '#!/bin/bash\nexit 73\n' > /usr/local/bin/unifi-os-legacy-layout
printf '#!/bin/bash\ntouch /run/prepare-started\n' > /usr/local/bin/unifi-os-prepare-mongo
if /usr/local/bin/unifi-os-bootstrap; then exit 1; else status=$?; fi
test "$status" = 73
test ! -e /run/prepare-started
test ! -e /var/lib/unifi-os-app/uos/state/server.conf
""")

    def test_worker_refuses_shared_or_unknown_mount_namespace(self):
        # Same namespace as PID 1, and an unreadable namespace link (both
        # readlinks empty), must both be refused before any umount.
        self.run_container(r"""
mkdir -p /run/stubs
printf '#!/bin/bash\ntouch /run/umount-called\n' > /run/stubs/umount
printf '#!/bin/bash\nexit 1\n' > /run/stubs/readlink
chmod 755 /run/stubs/*
if /usr/local/bin/unifi-os-worker service; then exit 1; fi
PATH=/run/stubs:$PATH
if /usr/local/bin/unifi-os-worker service; then exit 1; fi
test ! -e /run/umount-called
""")

    def test_failed_podman_info_does_not_publish_worker_readiness(self):
        worker = (APP / "rootfs/usr/local/bin/unifi-os-worker").read_text()
        prefix = "/bin/bash -euo pipefail -c '"
        self.assertIn(prefix, worker)
        child = worker.rsplit(prefix, 1)[1].rsplit("'", 1)[0]
        self.run_container(
            r"""
mkdir -p /run/user/1001 /var/lib/unifi-os-app/uos/state/bin
export XDG_RUNTIME_DIR=/run/user/1001
printf '#!/bin/bash\nexit 42\n' > /usr/bin/podman
printf '#!/bin/bash\ntouch /run/vendor-started\n' > /var/lib/uosserver/bin/uosserver-service
chmod 0755 /usr/bin/podman /var/lib/uosserver/bin/uosserver-service
if /bin/bash -euo pipefail -c "$1"; then exit 1; else status=$?; fi
test "$status" = 42
test ! -e /run/user/1001/unifi-rootless-ready
test ! -e /run/vendor-started
""",
            child,
        )
