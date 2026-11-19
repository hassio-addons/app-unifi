"""Opt-in real MongoDB format/FCV test; disposable, network-free container."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from docker_fixture import run_container

APP = Path(__file__).resolve().parents[1]


class MongodLauncherArgumentsTest(unittest.TestCase):
    """The vendor-path launcher filters MMAPv1 switches for MongoDB >= 4.2."""

    def launch(self, selection, *arguments):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = root / "runner"
            runner.write_text(
                '#!/bin/bash\nfor a in "$@"; do printf "%s\\n" "$a"; done\n'
            )
            runner.chmod(0o755)
            selected = root / "mongo-runtime"
            if selection is not None:
                selected.write_text(selection + "\n")
            launcher = root / "mongod"
            launcher.write_text(
                (APP / "rootfs/usr/lib/unifi/bin/mongod")
                .read_text()
                .replace("/var/lib/unifi-os-app/mongo-runtime", str(selected))
                .replace("/usr/local/bin/unifi-os-mongo", str(runner))
            )
            result = subprocess.run(
                ["bash", str(launcher), *arguments],
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout.splitlines()

    def test_smallfiles_is_dropped_only_for_runtimes_without_mmapv1(self):
        legacy = ("--dbpath", "/lib/unifi/data/db", "--smallfiles", "--nojournal")
        for selection in ("4.2", "4.4"):
            with self.subTest(selection=selection):
                self.assertEqual(
                    self.launch(selection, *legacy, "--smallfiles=true"),
                    [
                        selection,
                        "mongod",
                        "--dbpath",
                        "/lib/unifi/data/db",
                        "--nojournal",
                    ],
                )
        for selection in (None, "3.6", "4.0"):
            with self.subTest(selection=selection):
                self.assertEqual(
                    self.launch(selection, *legacy),
                    [selection or "3.6", "mongod", *legacy],
                )

    def test_no_arguments_and_unrelated_values_pass_through(self):
        self.assertEqual(self.launch("4.4"), ["4.4", "mongod"])
        self.assertEqual(
            self.launch("4.4", "--logpath", "--smallfiles.log"),
            ["4.4", "mongod", "--logpath", "--smallfiles.log"],
        )


@unittest.skipUnless(
    os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
)
class MongoUpgradeTest(unittest.TestCase):
    def test_legal_legacy_fcv34_and_interrupted_prewrite_backup_are_recoverable(self):
        run_container(
            r"""
runner=/usr/local/bin/unifi-os-mongo
mkdir -p /lib/unifi/data/db /var/lib/unifi-os-app/mongo-originals
touch /lib/unifi/data/system.properties
"$runner" 3.6 mongod --dbpath /lib/unifi/data/db --bind_ip 127.0.0.1 --port 27118 --nounixsocket --wiredTigerCacheSizeGB 0.25 --logpath /run/test.log &
pid=$!
for ((i=0;i<60;i++)); do
 if "$runner" 3.6 mongo --quiet --port 27118 --eval 'quit(db.adminCommand({ping:1}).ok === 1 ? 0 : 1)' >/dev/null 2>&1; then break; fi
 sleep 1
done
"$runner" 3.6 mongo --quiet --port 27118 --eval 'assert.commandWorked(db.adminCommand({setFeatureCompatibilityVersion:"3.4"})); db.getSiblingDB("ace").devices.insert({_id:"older-fcv-device"});'
kill -TERM "$pid"
wait "$pid"
printf 'interrupted staging\n' > /var/lib/unifi-os-app/mongo-originals/originals.tar.tmp
printf 'orphan staging digest\n' > /var/lib/unifi-os-app/mongo-originals/originals.sha256
mkdir /var/lib/unifi-os-app/mongo-originals/interrupted.KEEP01
printf 'not staging\n' > /var/lib/unifi-os-app/mongo-originals/interrupted.KEEP01/foreign
/usr/local/bin/unifi-os-prepare-mongo
test "$(cat /var/lib/unifi-os-app/mongo-runtime)" = 4.4
test "$(cat /var/lib/unifi-os-app/mongo-prepared)" = 4.4
# Staging moved aside before the verified archive existed is reaped only
# afterwards; a directory with unexpected contents is never removed.
test "$(find /var/lib/unifi-os-app/mongo-originals -mindepth 1 -maxdepth 1 -type d -name 'interrupted.*')" = \
    /var/lib/unifi-os-app/mongo-originals/interrupted.KEEP01
test -f /var/lib/unifi-os-app/mongo-originals/interrupted.KEEP01/foreign
(cd /var/lib/unifi-os-app/mongo-originals && sha256sum --status --check originals.sha256)
mkdir /run/original-fcv
tar -xf /var/lib/unifi-os-app/mongo-originals/originals.tar -C /run/original-fcv
"$runner" 3.6 mongod --dbpath /run/original-fcv/db --bind_ip 127.0.0.1 --port 27118 --nounixsocket --wiredTigerCacheSizeGB 0.25 --logpath /run/test.log &
pid=$!
for ((i=0;i<60;i++)); do
 if "$runner" 3.6 mongo --quiet --port 27118 --eval 'quit(db.adminCommand({ping:1}).ok === 1 ? 0 : 1)' >/dev/null 2>&1; then break; fi
 sleep 1
done
"$runner" 3.6 mongo --quiet --port 27118 --eval 'assert.eq("3.4",db.adminCommand({getParameter:1,featureCompatibilityVersion:1}).featureCompatibilityVersion.version);assert.eq(1,db.getSiblingDB("ace").devices.count());'
kill -TERM "$pid"
wait "$pid"
""",
            timeout=300,
        )

    def test_existing_final_archive_without_checksum_is_never_blessed(self):
        run_container(
            r"""
mkdir -p /lib/unifi/data/db /var/lib/unifi-os-app/mongo-originals
touch /lib/unifi/data/system.properties /lib/unifi/data/db/WiredTiger
printf 'unverified final archive\n' > /var/lib/unifi-os-app/mongo-originals/originals.tar
before=$(sha256sum /var/lib/unifi-os-app/mongo-originals/originals.tar)
if /usr/local/bin/unifi-os-prepare-mongo; then exit 1; fi
test ! -e /var/lib/unifi-os-app/mongo-originals/originals.sha256
test "$before" = "$(sha256sum /var/lib/unifi-os-app/mongo-originals/originals.tar)"
""",
            timeout=30,
        )

    def test_upgrade_failure_resume_noop_and_original_restore(self):
        run_container(
            r"""
runner=/usr/local/bin/unifi-os-mongo
prepare=/usr/local/bin/unifi-os-prepare-mongo
mkdir -p /lib/unifi/data/db
touch /lib/unifi/data/system.properties
start() {
    "$runner" "$1" mongod --dbpath "$2" --bind_ip 127.0.0.1 \
        --port 27118 --nounixsocket --wiredTigerCacheSizeGB 0.25 --logpath /run/test.log &
    pid=$!
    for ((i=0;i<60;i++)); do
        if "$runner" "$1" mongo --quiet --port 27118 --eval 'quit(db.adminCommand({ping:1}).ok === 1 ? 0 : 1)' >/dev/null 2>&1; then return; fi
        sleep 1
    done
    exit 1
}
stop() { kill -TERM "$pid"; wait "$pid"; }
for version in 3.6 4.0 4.2 4.4; do
    "$runner" "$version" mongod --version | head -1
done
start 3.6 /lib/unifi/data/db
"$runner" 3.6 mongo --quiet --port 27118 --eval \
    'db.getSiblingDB("ace").devices.insert({_id:"retained-device",config:{vlan:42}})'
stop

# Fail before the 4.2 FCV write, then prove restart uses MongoDB's checkpoint.
cp "$runner" /usr/local/bin/mongo-real
cat > "$runner" <<'WRAPPER'
#!/bin/bash
if [[ $1 == 4.2 && $2 == mongo && "$*" == *setFeatureCompatibilityVersion* ]]; then exit 42; fi
exec /usr/local/bin/mongo-real "$@"
WRAPPER
if "$prepare"; then echo 'Expected injected failure' >&2; exit 1; fi
test "$(cat /var/lib/unifi-os-app/mongo-runtime)" = 4.2
test ! -e /var/lib/unifi-os-app/mongo-prepared
before=$(sha256sum /var/lib/unifi-os-app/mongo-originals/originals.tar)
cp /usr/local/bin/mongo-real "$runner"
# Simulate an interruption after the 4.2 FCV step but before the runtime
# selection advanced: the next run must still continue to 4.4.
start 4.2 /lib/unifi/data/db
"$runner" 4.2 mongo --quiet --port 27118 --eval \
    'assert.commandWorked(db.adminCommand({setFeatureCompatibilityVersion:"4.2"}))'
stop
"$prepare"
test "$(cat /var/lib/unifi-os-app/mongo-runtime)" = 4.4
test "$(cat /var/lib/unifi-os-app/mongo-prepared)" = 4.4
"$prepare"
# With the vendor OS installed, a prepared database is not reopened at all.
mkdir -p /var/lib/unifi-os-app/uos/bootstrap
printf '5.1.42\n' > /var/lib/unifi-os-app/uos/bootstrap/installed-version
printf '#!/bin/bash\nexit 42\n' > "$runner"
"$prepare"
cp /usr/local/bin/mongo-real "$runner"
rm /var/lib/unifi-os-app/uos/bootstrap/installed-version
test "$before" = "$(sha256sum /var/lib/unifi-os-app/mongo-originals/originals.tar)"
test "$(stat -c %a /var/lib/unifi-os-app/mongo-originals)" = 700
test "$(stat -c %a /var/lib/unifi-os-app/mongo-originals/originals.tar)" = 600
start 4.4 /lib/unifi/data/db
"$runner" 4.4 mongo --quiet --port 27118 --eval \
    'assert.eq("4.4",db.adminCommand({getParameter:1,featureCompatibilityVersion:1}).featureCompatibilityVersion.version); assert.eq(42,db.getSiblingDB("ace").devices.findOne({_id:"retained-device"}).config.vlan)'
stop

# Restore only into a different disposable directory, never automatic rollback.
mkdir /run/rollback
tar -xf /var/lib/unifi-os-app/mongo-originals/originals.tar -C /run/rollback
start 3.6 /run/rollback/db
"$runner" 3.6 mongo --quiet --port 27118 --eval \
    'assert.eq("3.6",db.adminCommand({getParameter:1,featureCompatibilityVersion:1}).featureCompatibilityVersion.version); assert.eq(42,db.getSiblingDB("ace").devices.findOne({_id:"retained-device"}).config.vlan)'
stop

# An altered original archive must block startup rather than overwrite it.
printf 'corruption' >> /var/lib/unifi-os-app/mongo-originals/originals.tar
if "$prepare"; then echo 'Expected backup integrity failure' >&2; exit 1; fi
test "$(cat /var/lib/unifi-os-app/mongo-runtime)" = 4.4
""",
            timeout=300,
        )
