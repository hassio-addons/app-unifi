"""Root-executed vendor binaries must stay out of UID 1001's reach."""

import os
import unittest

from docker_fixture import run_container


@unittest.skipUnless(
    os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
)
class StateOwnershipTest(unittest.TestCase):
    def test_state_is_root_owned_with_only_runtime_entries_for_uosserver(self):
        run_container(
            r"""
root=/run/uos
mkdir -p "$root/state/bin" "$root/state/logs"
printf 'APP_VERSION=5.1.42\n' > "$root/state/server.conf"
printf '{}' > "$root/state/portmap.json"
printf 'log\n' > "$root/state/logs/inner.log"
chown -R uosserver:uosserver "$root/state"
chown root:root "$root/state/bin" "$root/state/portmap.json"
chmod 0777 "$root/state"
mkdir -p "$root/home"
chown uosserver:uosserver "$root/home"
chmod 0700 "$root"
/usr/local/bin/unifi-os-state-ownership "$root"
test "$(stat -c %U:%G:%a "$root")" = root:root:755
runuser -u uosserver -- mkdir -p "$root/home/.config/containers"
test "$(stat -c %U:%G:%a "$root/state")" = root:root:755
test "$(stat -c %U "$root/state/logs")" = uosserver
test "$(stat -c %U:%a "$root/state/server.conf")" = uosserver:640
test "$(stat -c %U "$root/state/portmap.json")" = root
# Not recursive: contents of the runtime entries are left alone.
chown root:root "$root/state/logs/inner.log"
/usr/local/bin/unifi-os-state-ownership "$root"
test "$(stat -c %U "$root/state/logs/inner.log")" = root
# A planted symlink is refused, never followed by chown.
rm "$root/state/server.conf"
ln -s /etc/shadow "$root/state/server.conf"
if /usr/local/bin/unifi-os-state-ownership "$root" 2>/dev/null; then exit 1; fi
test "$(stat -c %U /etc/shadow)" = root
""",
            timeout=30,
        )

    def test_exec_guard_refuses_any_non_root_or_writable_path_component(self):
        run_container(
            r"""
guard=/usr/local/bin/unifi-os-exec-guard
mkdir -p /opt/vendor/bin
printf '#!/bin/sh\n' > /opt/vendor/bin/service
chmod 0755 /opt/vendor/bin/service
ln -s /opt/vendor /opt/alias
$guard /opt/vendor/bin/service
$guard /opt/alias/bin/service
refused() { if $guard "$@" 2>/dev/null; then echo "accepted: $*" >&2; exit 1; fi; }
chown uosserver /opt/vendor
refused /opt/alias/bin/service
chown root /opt/vendor
chmod 0775 /opt/vendor/bin
refused /opt/vendor/bin/service
chmod 0755 /opt/vendor/bin
chmod 0757 /opt/vendor/bin/service
refused /opt/vendor/bin/service
chmod 0755 /opt/vendor/bin/service
chown uosserver /opt/vendor/bin/service
refused /opt/vendor/bin/service
chown root /opt/vendor/bin/service
chmod 0644 /opt/vendor/bin/service
refused /opt/vendor/bin/service
chmod 0755 /opt/vendor/bin/service
refused /opt/vendor/bin/missing
refused relative/path
ln -s /opt/vendor/bin/service /tmp/link
chown -h uosserver /tmp/link
refused /tmp/link
$guard /opt/vendor/bin/service
""",
            timeout=30,
        )
