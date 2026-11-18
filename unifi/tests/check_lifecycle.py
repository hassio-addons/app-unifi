"""Opt-in blank-data nested-OS lifecycle test; never touches an HA installation."""

import json
import os
import subprocess
import time
from uuid import uuid4


def main():
    image = os.environ["UNIFI_OS_LIFECYCLE_TEST_IMAGE"]
    prefix = f"unifi-lifecycle-{uuid4().hex}"
    container = prefix
    volumes = [prefix + suffix for suffix in ("-data", "-backup", "-restored")]

    def docker(*args, check=True, timeout=60):
        try:
            return subprocess.run(
                ["docker", *args],
                check=check,
                capture_output=True,
                text=True,
                timeout=timeout,
            ).stdout.strip()
        except subprocess.CalledProcessError as error:
            # Show the failing step's output in CI; never contains credentials.
            print(error.stdout[-4000:], error.stderr[-4000:], sep="\n")
            raise

    def start(volume):
        docker(
            "run",
            "-d",
            "--name",
            container,
            "--cgroupns=private",
            "--cap-add=SYS_ADMIN",
            "--device=/dev/net/tun",
            "--security-opt=apparmor=unifi",
            # Match Supervisor's observed SYS_ADMIN sandbox on HAOS. The
            # custom AppArmor profile remains enforcing throughout this test.
            "--security-opt=seccomp=unconfined",
            "--mount",
            f"type=volume,src={volume},dst=/lib/unifi/data",
            image,
        )

    def healthy():
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["docker", "exec", container, "/usr/local/bin/unifi-os-health"],
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0:
                return
            state = json.loads(docker("inspect", container))[0]["State"]
            if not state["Running"]:
                raise RuntimeError("Candidate exited during startup")
            failed = subprocess.run(
                [
                    "docker",
                    "exec",
                    container,
                    "systemctl",
                    "is-failed",
                    "--quiet",
                    "unifi-os-bootstrap.service",
                ],
                capture_output=True,
                timeout=30,
            )
            if failed.returncode == 0:
                raise RuntimeError(
                    "Bootstrap failed; not waiting for the health timeout"
                )
            time.sleep(5)
        raise TimeoutError("Nested vendor OS did not become healthy")

    def state():
        return docker(
            "exec",
            container,
            "bash",
            "-euc",
            """
cat /var/lib/unifi-os-app/uos/machine-id
sha256sum /opt/unifi-os/installer /var/lib/uosserver/bin/uosserver-service /var/lib/uosserver/bin/updater-service
cmp /etc/systemd/system/uosserver.service /var/lib/unifi-os-app/uos/bootstrap/uosserver.service
cmp /etc/systemd/system/uosserver-updater.service /var/lib/unifi-os-app/uos/bootstrap/uosserver-updater.service
cat /proc/1/attr/current
runuser -u uosserver -- env XDG_RUNTIME_DIR=/run/user/1001 podman exec uosserver cat /proc/1/attr/current
test "$(systemctl is-enabled nginx.service || true)" = masked
test "$(systemctl is-enabled unifi-os-ingress.service)" = enabled
systemctl is-active --quiet unifi-os-ingress.service
grep -Fxq 'map "" $ingress_http_compat_enabled { default 0; }' /run/unifi-os-ingress-policy.conf
for attempt in $(seq 50); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -H 'X-Ingress-Path: /api/hassio_ingress/probe' http://127.0.0.1:8099/ || true)
    [[ "$code" == 000 ]] || break
    sleep 0.2
done
test "$code" = 403
""",
        )

    def root_sysrq_boundary():
        docker(
            "exec",
            container,
            "python3",
            "-c",
            """
import errno, os
path = '/sysrq-trigger'
assert not os.path.lexists(path)
try:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
except OSError as error:
    assert error.errno in (errno.EACCES, errno.EROFS)
else:
    os.close(descriptor)
    os.unlink(path)
    raise RuntimeError('Root-level protected basename is not write-denied')
""",
        )
        # Directory source/target names are slash-terminated in effective mount
        # mediation. This parent bind must fail; never write any kernel control.
        docker(
            "exec",
            container,
            "unshare",
            "--mount",
            "--propagation",
            "private",
            "/bin/bash",
            "-euc",
            """
mkdir /run/controls-lifecycle-probe
trap 'umount /run/controls-lifecycle-probe 2>/dev/null || true; rmdir /run/controls-lifecycle-probe' EXIT
if mount --bind /proc/sys/ /run/controls-lifecycle-probe/; then
    echo 'Renamed kernel parent bind unexpectedly allowed' >&2
    exit 1
fi
""",
        )

    try:
        for volume in volumes:
            docker("volume", "create", volume)
        start(volumes[0])
        healthy()
        root_sysrq_boundary()
        # The ingress unit must regenerate a missing policy, not skip starting.
        docker(
            "exec",
            container,
            "bash",
            "-euc",
            """
rm /run/unifi-os-ingress-policy.conf
systemctl restart unifi-os-ingress.service
systemctl is-active --quiet unifi-os-ingress.service
grep -Fxq 'map "" $ingress_http_compat_enabled { default 0; }' /run/unifi-os-ingress-policy.conf
for attempt in $(seq 50); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -H 'X-Ingress-Path: /api/hassio_ingress/probe' http://127.0.0.1:8099/ || true)
    [[ "$code" == 000 ]] || break
    sleep 0.2
done
test "$code" = 403
# The running ingress unit must hold the mandatory read-only projection (a
# missing source would have failed its start), and a port published after a
# reader started must be visible through that same bind without a restart.
# Docker grants no CAP_SYS_PTRACE, so instead of entering nginx's namespace an
# equivalent transient DynamicUser reader checks contents and mount flags. The
# blank lab has no HA token, so write as the publisher's static user.
systemctl is-active --quiet unifi-os-ingress.service
test "$(systemctl show unifi-os-ingress.service --property=BindReadOnlyPaths --value)" = /run/unifi-os-console-port:/run/unifi-os-ingress-port:rbind
test "$(stat -c %U:%a /run/unifi-os-console-port)" = unifi-port:755
rm -f /run/unifi-os-console-port/secure-port.json /run/projection-reader.out
systemd-run --wait --quiet --collect --pipe -p DynamicUser=yes \
    -p BindReadOnlyPaths=/run/unifi-os-console-port:/run/unifi-os-ingress-port \
    /bin/sh -c 'flags=$(grep " /run/unifi-os-ingress-port " /proc/self/mountinfo | cut -d" " -f6)
        case ",$flags," in *,ro,*) ;; *) echo "NOT-READ-ONLY:$flags"; exit 1 ;; esac
        [ ! -e /run/unifi-os-ingress-port/secure-port.json ] || { echo PRESENT-TOO-EARLY; exit 1; }
        echo READY
        for i in $(seq 100); do [ -s /run/unifi-os-ingress-port/secure-port.json ] && break; sleep 0.1; done
        cat /run/unifi-os-ingress-port/secure-port.json' > /run/projection-reader.out &
reader=$!
for _ in $(seq 100); do grep -qx READY /run/projection-reader.out 2>/dev/null && break; sleep 0.1; done
grep -qx READY /run/projection-reader.out
setpriv --reuid unifi-port --regid unifi-port --clear-groups \
    /bin/sh -c 'printf "{\\"port\\":11441}" > /run/unifi-os-console-port/secure-port.json'
wait "$reader"
test "$(tail -n 1 /run/projection-reader.out)" = '{"port":11441}'
rm /run/unifi-os-console-port/secure-port.json /run/projection-reader.out
""",
        )
        before = state()
        docker("stop", "--time=300", container, timeout=320)
        docker("start", container)
        healthy()
        assert state() == before, "Restart changed identity/vendor bytes/profile"
        docker("stop", "--time=300", container, timeout=320)
        docker("rm", container)
        start(volumes[0])
        healthy()
        assert state() == before, "Recreation changed identity/vendor bytes/profile"
        docker("stop", "--time=300", container, timeout=320)
        docker("rm", container)
        docker(
            "run",
            "--rm",
            "--name",
            prefix + "-backup-helper",
            "--network=none",
            "--entrypoint=bash",
            "--mount",
            f"type=volume,src={volumes[0]},dst=/input,readonly",
            "--mount",
            f"type=volume,src={volumes[1]},dst=/backup",
            image,
            "-euc",
            """
cd /input/uos
test ! -e runtime-checkpoint.incomplete
test ! -e runtime-restore.incomplete
sha256sum --status --check runtime-checkpoint.tar.gz.sha256
tar --numeric-owner --xattrs --acls --exclude='./uos/home' --exclude='./uos/state' \
    -cpf /backup/app.tar -C /input .
""",
            timeout=300,
        )
        docker(
            "run",
            "--rm",
            "--name",
            prefix + "-restore-helper",
            "--network=none",
            "--entrypoint=tar",
            "--mount",
            f"type=volume,src={volumes[1]},dst=/backup,readonly",
            "--mount",
            f"type=volume,src={volumes[2]},dst=/output",
            image,
            "--numeric-owner",
            "--xattrs",
            "--xattrs-include=*",
            "--same-owner",
            "--same-permissions",
            "--acls",
            "-xpf",
            "/backup/app.tar",
            "-C",
            "/output",
            timeout=300,
        )
        start(volumes[2])
        healthy()
        assert state() == before, (
            "Checkpoint restore changed identity/vendor bytes/profile"
        )
        print(
            "Nested OS install/restart/recreation/opaque-checkpoint restore checks passed."
        )
    except BaseException:
        print(docker("logs", "--tail=80", container, check=False))
        print(
            docker(
                "exec",
                container,
                "journalctl",
                "--no-pager",
                "-n",
                "120",
                "-u",
                "dbus.service",
                "-u",
                "systemd-logind.service",
                "-u",
                "user@1001.service",
                "-u",
                "unifi-os-bootstrap.service",
                check=False,
            )
        )
        raise
    finally:
        for name in (container, prefix + "-backup-helper", prefix + "-restore-helper"):
            docker("rm", "--force", name, check=False, timeout=30)
        for volume in volumes:
            docker("volume", "rm", volume, check=False, timeout=30)


if __name__ == "__main__":
    main()
