"""OS updates must use the vendor path, forbid downgrades and fail closed."""

import os
import unittest

from docker_fixture import run_container

SETUP = r"""
saved=/var/lib/unifi-os-app/uos/bootstrap
mkdir -p "$saved" /var/lib/unifi-os-app/uos/state /run/stubs
for command in uosserver uosserver-purge; do printf '#!/bin/bash\nexit 0\n' > "$saved/$command"; done
for unit in uosserver.service uosserver-updater.service; do printf '[Unit]\nDescription=synthetic old unit\n' > "$saved/$unit"; done
printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> /run/systemctl.calls\n' > /run/stubs/systemctl
printf '#!/bin/bash\nexit 0\n' > /run/stubs/loginctl
printf '#!/bin/bash\nshift 3\nexec "$@"\n' > /run/stubs/unshare
cat > /usr/local/bin/unifi-os-worker <<'WORKER'
#!/bin/bash
set -euo pipefail
[[ "$*" == installer ]]
printf '%s\n' "$*" > /run/worker.called
if [[ "${FAIL_INSTALLER:-}" == before ]]; then exit 42; fi
printf 'APP_VERSION=%s\n' "$(cat /opt/unifi-os/version)" > /var/lib/uosserver/server.conf
if [[ "${FAIL_INSTALLER:-}" == partial ]]; then exit 42; fi
for unit in uosserver.service uosserver-updater.service; do
    printf '[Unit]\nDescription=synthetic updated unit\n' > /etc/systemd/system/$unit
done
WORKER
chmod 0755 /run/stubs/* /usr/local/bin/unifi-os-worker
export PATH=/run/stubs:$PATH
"""


@unittest.skipUnless(
    os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
)
class OsUpdatePolicyTest(unittest.TestCase):
    def test_matching_pinned_restart_starts_both_vendor_services_without_install(self):
        run_container(
            SETUP
            + r"""
printf '5.1.42\n' > "$saved/installed-version"
printf 'APP_VERSION=5.1.42\n' > /var/lib/uosserver/server.conf
/usr/local/bin/unifi-os-bootstrap
grep -Fxq 'start uosserver.service uosserver-updater.service' /run/systemctl.calls
test ! -e /run/worker.called
test "$(cat "$saved/installed-version")" = 5.1.42
""",
            timeout=30,
        )

    def test_supported_upgrade_uses_unchanged_worker_mode_and_updates_receipt_last(
        self,
    ):
        run_container(
            SETUP
            + r"""
printf '5.1.41\n' > "$saved/installed-version"
printf 'APP_VERSION=5.1.41\n' > /var/lib/uosserver/server.conf
/usr/local/bin/unifi-os-bootstrap
test "$(cat /run/worker.called)" = installer
test "$(cat "$saved/installed-version")" = 5.1.42
cmp /etc/systemd/system/uosserver.service "$saved/uosserver.service"
! grep -Eq ' stop |force-install' /run/systemctl.calls
""",
            timeout=30,
        )

    def test_downgrade_and_version_drift_never_start_installer(self):
        run_container(
            SETUP
            + r"""
for pair in '5.1.43 5.1.43' '5.1.41 5.1.42'; do
    read -r receipt configuration <<< "$pair"
    printf '%s\n' "$receipt" > "$saved/installed-version"
    printf 'APP_VERSION=%s\n' "$configuration" > /var/lib/uosserver/server.conf
    if /usr/local/bin/unifi-os-bootstrap; then exit 1; fi
    test ! -e /run/worker.called
    test "$(cat "$saved/installed-version")" = "$receipt"
done
""",
            timeout=30,
        )

    def test_failed_and_partial_vendor_update_cannot_mark_success_or_blindly_retry(
        self,
    ):
        run_container(
            SETUP
            + r"""
printf '5.1.41\n' > "$saved/installed-version"
printf 'APP_VERSION=5.1.41\n' > /var/lib/uosserver/server.conf
for phase in before partial; do
    export FAIL_INSTALLER=$phase
    if /usr/local/bin/unifi-os-bootstrap; then exit 1; fi
    test "$(cat "$saved/installed-version")" = 5.1.41
done
rm /run/worker.called
unset FAIL_INSTALLER
if /usr/local/bin/unifi-os-bootstrap; then exit 1; fi
test ! -e /run/worker.called
""",
            timeout=30,
        )
