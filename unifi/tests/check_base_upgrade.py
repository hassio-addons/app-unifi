"""Upgrade real data written by the previous app release with the candidate.

The previous release (5.3.2) is started on a blank disposable volume so its own
s6 init writes the actual retained layout and its own MongoDB 3.6 creates the
database. After a normal stop, the candidate starts on that same volume where
the new manifest maps Supervisor data. Never touches an HA installation.
"""

import json
import os
import subprocess
import sys
import time
from uuid import uuid4

LEGACY_IMAGE = (
    "ghcr.io/hassio-addons/unifi:5.3.2"
    "@sha256:9b1b59e7fcbe4d3d016782a5f6120753cd8a20252270f99eb231c05e3f338495"
)
MONGO_STEPS = ("3.6", "4.0", "4.2", "4.4")


def main():
    image = os.environ["UNIFI_OS_UPGRADE_TEST_IMAGE"]
    prefix = f"unifi-base-upgrade-{uuid4().hex}"
    legacy = prefix + "-legacy"
    candidate = prefix + "-candidate"
    helper = prefix + "-helper"
    data, backup = prefix + "-data", prefix + "-backup"

    def docker(*args, check=True, timeout=60):
        try:
            return subprocess.run(
                ["docker", *args],
                check=check,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.CalledProcessError as error:
            # Show the failing step's output in CI; never contains credentials.
            print(error.stdout[-4000:], error.stderr[-4000:], sep="\n", flush=True)
            raise

    def running(name):
        return json.loads(docker("inspect", name).stdout)[0]["State"]

    def cold(script, *mounts, timeout=300):
        return docker(
            "run",
            "--rm",
            "--name",
            helper,
            "--network=none",
            "--entrypoint=bash",
            *mounts,
            image,
            "-euo",
            "pipefail",
            "-c",
            script,
            timeout=timeout,
        ).stdout

    def mount(volume, path, readonly=False):
        options = ",readonly" if readonly else ""
        return "--mount", f"type=volume,src={volume},dst={path}{options}"

    def data_at(path, readonly=False):
        return mount(data, path, readonly)

    def backup_at(path, readonly=False):
        return mount(backup, path, readonly)

    def start_legacy():
        # Previous manifest: Supervisor data at /data, backup:rw. Its controller
        # creates the database locally; no network or Supervisor is needed.
        docker(
            "run",
            "-d",
            "--name",
            legacy,
            "--network=none",
            *data_at("/data"),
            *backup_at("/backup"),
            LEGACY_IMAGE,
        )
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            state = running(legacy)
            if not state["Running"]:
                raise RuntimeError(f"Previous app exited early: {state}")
            probe = docker(
                "exec",
                legacy,
                "bash",
                "-euc",
                """
test -f /data/unifi/data/db/WiredTiger
if command -v mongo >/dev/null; then
    mongo --quiet --host 127.0.0.1 --port 27117 \
        --eval 'quit(db.getMongo().getDBNames().indexOf("ace") >= 0 ? 0 : 1)' >/dev/null
    echo 'ace database present (mongo shell)'
else
    curl -kfsS --max-time 5 -o /dev/null https://127.0.0.1:8443/
    echo 'controller serving 8443 (no mongo shell in previous image)'
fi
""",
                check=False,
                timeout=30,
            )
            if probe.returncode == 0:
                print("Previous app database ready:", probe.stdout.strip(), flush=True)
                return
            time.sleep(5)
        raise TimeoutError("Previous app did not create its database in time")

    def start_candidate():
        # Same outer sandbox as check_lifecycle.py, plus the new manifest's
        # read-only backup map so the retained backup symlink resolves.
        docker(
            "run",
            "-d",
            "--name",
            candidate,
            "--cgroupns=private",
            "--cap-add=SYS_ADMIN",
            "--device=/dev/net/tun",
            "--security-opt=apparmor=unifi",
            "--security-opt=seccomp=unconfined",
            *data_at("/lib/unifi/data"),
            *backup_at("/backup", readonly=True),
            image,
        )

    def healthy():
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            result = docker(
                "exec",
                candidate,
                "/usr/local/bin/unifi-os-health",
                check=False,
                timeout=30,
            )
            if result.returncode == 0:
                return
            if not running(candidate)["Running"]:
                raise RuntimeError("Candidate exited during upgrade startup")
            failed = docker(
                "exec",
                candidate,
                "systemctl",
                "is-failed",
                "--quiet",
                "unifi-os-bootstrap.service",
                "unifi.service",
                check=False,
                timeout=30,
            )
            if failed.returncode == 0:
                raise RuntimeError("Layout/bootstrap failed on previous-release data")
            time.sleep(10)
        raise TimeoutError("Candidate did not become healthy on previous-release data")

    failure = None
    try:
        docker("pull", LEGACY_IMAGE, timeout=900)
        for volume in (data, backup):
            docker("volume", "create", volume)
        # Supervisor writes the previous schema's (empty) options before start.
        cold("printf '{}\\n' > /data/options.json", *data_at("/data"))
        start_legacy()
        docker("stop", "--time=120", legacy, timeout=150)
        print("Previous app stop state:", running(legacy), flush=True)
        # The layout must be the previous app's own, not a manufactured one.
        print(
            cold(
                """
grep -Fxq 'unifi.db.extraargs=--smallfiles' /data/unifi/data/system.properties
test -L /data/unifi/data/backup
test "$(readlink /data/unifi/data/backup)" = /backup/unifi
test -d /backup/unifi
test -f /data/unifi/data/db/WiredTiger
test ! -e /data/uos
if [[ -s /data/unifi/data/db/mongod.lock ]]; then
    echo 'Previous app left MongoDB unclean; journal recovery is exercised.'
else
    echo 'Previous app shut MongoDB down cleanly.'
fi
du -sh /data/unifi/data/db
""",
                *data_at("/data", readonly=True),
                *backup_at("/backup", readonly=True),
            ),
            flush=True,
        )
        docker("rm", legacy)
        # Supervisor writes the new schema's defaults before starting the update.
        cold(
            "printf '{\"ingress_allow_insecure_http\": false}\\n' > /data/options.json",
            *data_at("/data"),
        )
        start_candidate()
        healthy()
        docker(
            "exec",
            candidate,
            "bash",
            "-euc",
            """
# Retained unifi/data is a real bind mount at the vendor path, not a symlink.
systemctl is-active --quiet unifi.service
test ! -L /lib/unifi/data
mountpoint -q /lib/unifi/data
test "$(stat -c %d:%i /lib/unifi/data)" = "$(stat -c %d:%i /var/lib/unifi-os-app/unifi/data)"
test -f /lib/unifi/data/system.properties
test -f /lib/unifi/data/db/WiredTiger
# Conversion finished at 4.4 with a verified cold originals archive.
test "$(cat /var/lib/unifi-os-app/mongo-runtime)" = 4.4
cd /var/lib/unifi-os-app/mongo-originals
test -f originals.tar
test ! -e originals.tar.tmp
test ! -e originals.sha256.tmp
sha256sum --status --check originals.sha256
tar -tf originals.tar > /run/originals.list
grep -Fxq ./system.properties /run/originals.list
grep -Fxq ./db/WiredTiger /run/originals.list
rm /run/originals.list
# The unchanged vendor installer completed and was saved.
test "$(cat /var/lib/unifi-os-app/uos/bootstrap/installed-version)" = "$(cat /opt/unifi-os/version)"
grep -Fxq "APP_VERSION=$(cat /opt/unifi-os/version)" /var/lib/uosserver/server.conf
test "$(cat /proc/1/attr/current)" = 'unifi (enforce)'
""",
        )
        journal = docker(
            "exec",
            candidate,
            "journalctl",
            "--no-pager",
            "-o",
            "cat",
            "-u",
            "unifi-os-bootstrap.service",
            timeout=60,
        ).stdout
        logs = docker("logs", candidate)
        output = journal + logs.stdout + logs.stderr
        for version in MONGO_STEPS:
            line = f"Validated in-place MongoDB {version}, FCV {version}."
            assert line in output, f"Missing prepare-mongo checkpoint: {line}"
        assert "Cold legacy originals saved privately" in output, (
            "Originals archive was not created by this upgrade"
        )
        print(
            "Retained properties after upgrade:",
            docker(
                "exec",
                candidate,
                "grep",
                "-F",
                "unifi.db.extraargs",
                "/var/lib/unifi-os-app/unifi/data/system.properties",
                check=False,
            ).stdout.strip(),
            flush=True,
        )
        docker("stop", "--time=300", candidate, timeout=320)
        # Independently confirm FCV on a cold copy; never reopen the volume itself.
        print(
            cold(
                """
cp -a /input/unifi/data/db /tmp/db
/usr/local/bin/unifi-os-mongo 4.4 mongod --dbpath /tmp/db --bind_ip 127.0.0.1 \
    --port 27119 --nounixsocket --logpath /tmp/mongod.log &
pid=$!
trap 'kill "${pid}" 2>/dev/null || true; wait "${pid}" || true' EXIT
query() { /usr/local/bin/unifi-os-mongo 4.4 mongo --quiet --host 127.0.0.1 --port 27119 --eval "$1"; }
for _ in $(seq 120); do
    kill -0 "${pid}" || { tail -n 40 /tmp/mongod.log; exit 1; }
    query 'quit(db.adminCommand({ping:1}).ok === 1 ? 0 : 1)' >/dev/null 2>&1 && break
    sleep 1
done
test "$(query 'print(db.version().split(".").slice(0,2).join("."))')" = 4.4
fcv=$(query 'var r=db.adminCommand({getParameter:1,featureCompatibilityVersion:1}); assert.commandWorked(r); assert(!r.featureCompatibilityVersion.targetVersion); print(r.featureCompatibilityVersion.version)')
test "${fcv}" = 4.4
query 'quit(db.getMongo().getDBNames().indexOf("ace") >= 0 ? 0 : 1)'
echo "Cold copy: MongoDB 4.4, FCV ${fcv}, ace database present."
""",
                *data_at("/input", readonly=True),
            ),
            flush=True,
        )
        print("Previous-release data upgrade checks passed.")
    except BaseException as error:
        failure = error
        diagnostics = [
            *((name, ("logs", "--tail=120", name)) for name in (legacy, candidate)),
            (
                candidate + " journal",
                (
                    "exec",
                    candidate,
                    "journalctl",
                    "--no-pager",
                    "-n",
                    "200",
                    "-u",
                    "unifi.service",
                    "-u",
                    "unifi-os-bootstrap.service",
                ),
            ),
        ]
        for label, args in diagnostics:
            try:
                result = docker(*args, check=False, timeout=30)
                print(f"--- {label} ---", result.stdout, result.stderr, sep="\n")
            except (OSError, subprocess.TimeoutExpired) as diagnostic_error:
                print(f"--- {label} unavailable: {type(diagnostic_error).__name__}")
        sys.stdout.flush()
        raise
    finally:
        errors = []
        for args in (
            *(("rm", "--force", name) for name in (legacy, candidate, helper)),
            *(("volume", "rm", volume) for volume in (data, backup)),
        ):
            try:
                docker(*args, check=False, timeout=60)
            except (OSError, subprocess.TimeoutExpired) as error:
                errors.append(error)
                print(f"Cleanup failed: {' '.join(args)}", file=sys.stderr)
        if errors and failure is None:
            raise errors[0]


if __name__ == "__main__":
    main()
