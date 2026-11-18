"""Native blank-data installer interruption and scoped cold-backup recovery."""

import json
import os
from pathlib import Path, PurePosixPath
import select
import signal
import subprocess
import sys
import tarfile
import time
from uuid import uuid4


def check_partial_refusal(state, logs):
    assert not state["Running"], "Partial installation is still running"
    assert state["ExitCode"] != 0, "Partial installation unexpectedly accepted"
    assert (
        "UniFi OS installation guard refused: partial/existing installation without receipt;"
        in logs
    ), "Startup failed for another reason; partial-state refusal was not verified"


def check_archive_pathnames(archive, root):
    def walk_error(error):
        raise error

    assert root.is_dir(), "Cold source root is missing"
    actual = {"."}
    for directory, directories, files in os.walk(
        root, followlinks=False, onerror=walk_error
    ):
        for name in directories + files:
            actual.add((Path(directory) / name).relative_to(root).as_posix())
    with tarfile.open(archive, "r:") as stream:
        expected = {str(PurePosixPath(member.name)) for member in stream}
    assert actual == expected, (
        f"Cold source pathname inventory differs: {len(actual - expected)} additions, "
        f"{len(expected - actual)} missing paths"
    )


def cleanup_created(docker, containers, volumes, failure):
    errors = []
    for resource, names in (("container", containers), ("volume", volumes)):
        for name in names:
            args = (
                ("rm", "--force", name)
                if resource == "container"
                else ("volume", "rm", name)
            )
            try:
                result = docker(*args, check=False, timeout=30)
                if result.returncode:
                    # --rm helpers may already be gone. Confirm absence via a
                    # successful list, not an arbitrary Docker error string.
                    listing = docker(
                        resource,
                        "ls",
                        *(("--all",) if resource == "container" else ()),
                        "--format",
                        "{{.Names}}" if resource == "container" else "{{.Name}}",
                        check=False,
                        timeout=10,
                    )
                    if (
                        listing.returncode == 0
                        and name not in listing.stdout.splitlines()
                    ):
                        continue
                    raise subprocess.CalledProcessError(
                        result.returncode,
                        result.args,
                        output=result.stdout,
                        stderr=result.stderr,
                    )
            except (
                OSError,
                subprocess.TimeoutExpired,
                subprocess.CalledProcessError,
            ) as error:
                errors.append(error)
                print(
                    f"Recovery fixture cleanup failed for {name}: {type(error).__name__}"
                )
    if errors and failure is None:
        raise errors[0]


def terminate_live_installer(pid, expected):
    """Freeze a verified live installer via pidfd, then terminate that same task."""
    descriptor = os.pidfd_open(pid)
    stopped = False
    killed = False
    try:
        poll = select.poll()
        poll.register(descriptor, select.POLLIN)
        assert not poll.poll(0), "Installer already exited before interruption"
        executable = Path(f"/proc/{pid}/exe")
        actual = executable.stat()
        assert (actual.st_dev, actual.st_ino) == expected, (
            "Installer executable changed"
        )
        # SIGSTOP prevents a normal completion between the liveness proof and
        # termination. pidfd prevents signaling a reused numeric PID.
        signal.pidfd_send_signal(descriptor, signal.SIGSTOP)
        stopped = True
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            assert not poll.poll(0), "Installer exited before it could be stopped"
            status = Path(f"/proc/{pid}/status").read_text()
            if any(
                line.startswith("State:") and line.split()[1] == "T"
                for line in status.splitlines()
            ):
                break
            time.sleep(0.01)
        else:
            raise TimeoutError("Installer did not enter stopped state")
        actual = executable.stat()
        assert (actual.st_dev, actual.st_ino) == expected, (
            "Stopped installer executable changed"
        )
        assert not Path(
            "/var/lib/unifi-os-app/uos/bootstrap/installed-version"
        ).exists()
        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        killed = True
        assert poll.poll(5000), "Terminated installer did not exit"
    finally:
        if stopped and not killed:
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGCONT)
            except ProcessLookupError:
                pass
        os.close(descriptor)


def interrupt_live_installer():
    assert os.environ.get("container") == "docker", (
        "Only run inside the blank test container"
    )
    binary = Path("/opt/unifi-os/installer").stat()
    expected = (binary.st_dev, binary.st_ino)
    matches = []
    cgroup = "/system.slice/unifi-os-bootstrap.service"
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            executable = (process / "exe").stat()
            groups = [
                line.split(":", 2)[2]
                for line in (process / "cgroup").read_text().splitlines()
            ]
            if (executable.st_dev, executable.st_ino) == expected and any(
                group == cgroup or group.startswith(cgroup + "/") for group in groups
            ):
                matches.append(int(process.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    assert len(matches) == 1, (
        "No unique live vendor installer; missed interruption window"
    )
    terminate_live_installer(matches[0], expected)
    print("Verified live vendor installer interrupted via pidfd.", flush=True)


def main():
    image = os.environ["UNIFI_OS_RECOVERY_TEST_IMAGE"]
    prefix = "unifi-install-recovery-" + uuid4().hex
    partial = prefix + "-partial"
    refused = prefix + "-refused"
    recovered = prefix + "-recovered"
    helper = prefix + "-helper"
    data, backup, restored = [
        prefix + suffix for suffix in ("-data", "-backup", "-restored")
    ]
    containers = [partial, refused, recovered, helper]
    volumes = [data, backup, restored]

    def docker(*args, check=True, timeout=60):
        return subprocess.run(
            ["docker", *args],
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def start(name, volume):
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--cgroupns=private",
            "--cap-add=SYS_ADMIN",
            "--device=/dev/net/tun",
            "--security-opt=apparmor=unifi",
            "--security-opt=seccomp=unconfined",
            "--mount",
            f"type=volume,src={volume},dst=/lib/unifi/data",
            "--mount",
            f"type=bind,src={Path(__file__).resolve()},dst=/fixture/recovery.py,readonly",
            image,
        )

    def cold_helper(source, destination, script, timeout=300):
        return docker(
            "run",
            "--rm",
            "--name",
            helper,
            "--network=none",
            "--entrypoint=bash",
            "--mount",
            f"type=volume,src={source},dst=/input,readonly",
            "--mount",
            f"type=volume,src={destination},dst=/output",
            "--mount",
            f"type=bind,src={Path(__file__).resolve()},dst=/fixture/recovery.py,readonly",
            image,
            "-euo",
            "pipefail",
            "-c",
            script,
            timeout=timeout,
        ).stdout

    failure = None
    try:
        for volume in volumes:
            docker("volume", "create", volume)
        docker(
            "run",
            "--rm",
            "--name",
            helper,
            "--network=none",
            "--entrypoint=bash",
            "--mount",
            f"type=volume,src={data},dst=/output",
            image,
            "-euc",
            "umask 077; printf 'pre-install recovery sentinel\\n' > /output/recovery-probe",
        )
        # A real, quiesced pre-install root, not a manufactured installation receipt.
        cold_helper(
            data,
            backup,
            """
tar --numeric-owner --xattrs --acls -cpf /output/pre-install.tar -C /input .
tar --numeric-owner --xattrs --acls -df /output/pre-install.tar -C /input
cd /output
sha256sum pre-install.tar > pre-install.tar.sha256
sync -f pre-install.tar
sync -f pre-install.tar.sha256
""",
        )
        start(partial, data)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            running = json.loads(docker("inspect", partial).stdout)[0]["State"][
                "Running"
            ]
            assert running, "Candidate exited before installer interruption"
            # Detect actual vendor-written state while the original installer
            # is still running. Never remove a receipt to simulate interruption.
            probe = docker(
                "exec",
                partial,
                "bash",
                "-euc",
                """
test -f /var/lib/uosserver/server.conf
test ! -e /var/lib/unifi-os-app/uos/bootstrap/installed-version
test "$(systemctl show unifi-os-bootstrap.service --property=ActiveState --value)" = activating
""",
                check=False,
            )
            if probe.returncode == 0:
                docker(
                    "exec",
                    partial,
                    "python3",
                    "/fixture/recovery.py",
                    "--interrupt-installer",
                )
                break
            complete = docker(
                "exec",
                partial,
                "test",
                "-e",
                "/var/lib/unifi-os-app/uos/bootstrap/installed-version",
                check=False,
            )
            assert complete.returncode != 0, (
                "Missed interruption window; no simulated pass"
            )
            failed = docker(
                "exec",
                partial,
                "systemctl",
                "is-failed",
                "--quiet",
                "unifi-os-bootstrap.service",
                check=False,
            )
            assert failed.returncode != 0, (
                "Installer failed before intentional interruption"
            )
            time.sleep(0.1)
        else:
            raise TimeoutError("No actual in-progress vendor installation observed")

        docker("stop", "--time=300", partial, timeout=320)
        # Keep the partial volume and a verified full raw archive intact while
        # testing refusal and recovery. Do not extract over the partial source.
        preserved = cold_helper(
            data,
            backup,
            """
test -f /input/uos/state/server.conf
test ! -e /input/uos/bootstrap/installed-version
tar --numeric-owner --xattrs --acls --one-file-system \
    -cpf /output/interrupted-install.tar -C /input .
tar --numeric-owner --xattrs --acls -df /output/interrupted-install.tar -C /input
python3 /fixture/recovery.py --compare-pathnames /output/interrupted-install.tar /input
cd /output
sha256sum interrupted-install.tar > interrupted-install.tar.sha256
sync -f interrupted-install.tar
sync -f interrupted-install.tar.sha256
sha256sum --check pre-install.tar.sha256 interrupted-install.tar.sha256
""",
        )
        print(
            "Interrupted vendor state preserved and source-compared:",
            preserved,
            flush=True,
        )
        start(refused, data)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = json.loads(docker("inspect", refused).stdout)[0]["State"]
            if not state["Running"]:
                break
            time.sleep(0.1)
        else:
            raise TimeoutError("Partial-state restart did not fail closed")
        logs = docker("logs", refused)
        check_partial_refusal(state, logs.stdout + logs.stderr)
        # Validate the archived partial source remained byte/metadata-identical
        # even after refused startup. Startup must not reinstall or bless it.
        cold_helper(
            data,
            backup,
            """
cd /output
sha256sum --check interrupted-install.tar.sha256
test ! -e /input/uos/bootstrap/installed-version
test ! -L /input/uos/bootstrap/installed-version
tar --numeric-owner --xattrs --acls -df interrupted-install.tar -C /input
python3 /fixture/recovery.py --compare-pathnames interrupted-install.tar /input
""",
        )
        cold_helper(
            backup,
            restored,
            """
test -z "$(find /output -mindepth 1 -maxdepth 1 -print -quit)"
cd /input
sha256sum --check pre-install.tar.sha256
tar --numeric-owner --same-owner --same-permissions --xattrs \
    --xattrs-include='*' --acls -xpf pre-install.tar -C /output
tar --numeric-owner --xattrs --acls -df pre-install.tar -C /output
test ! -e /output/uos/bootstrap/installed-version
""",
        )
        start(recovered, restored)
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            health = docker(
                "exec", recovered, "/usr/local/bin/unifi-os-health", check=False
            )
            if health.returncode == 0:
                break
            state = json.loads(docker("inspect", recovered).stdout)[0]["State"]
            assert state["Running"], "Recovered installation exited"
            failed = docker(
                "exec",
                recovered,
                "systemctl",
                "is-failed",
                "--quiet",
                "unifi-os-bootstrap.service",
                check=False,
            )
            assert failed.returncode != 0, "Recovered installer failed"
            time.sleep(5)
        else:
            raise TimeoutError("Recovered installation did not become healthy")
        docker(
            "exec",
            recovered,
            "bash",
            "-euc",
            """
printf 'pre-install recovery sentinel\\n' | cmp - /var/lib/unifi-os-app/recovery-probe
test "$(cat /var/lib/unifi-os-app/uos/bootstrap/installed-version)" = "$(cat /opt/unifi-os/version)"
grep -Fx "APP_VERSION=$(cat /opt/unifi-os/version)" /var/lib/uosserver/server.conf
cmp /etc/systemd/system/uosserver.service /var/lib/unifi-os-app/uos/bootstrap/uosserver.service
cmp /etc/systemd/system/uosserver-updater.service /var/lib/unifi-os-app/uos/bootstrap/uosserver-updater.service
test "$(cat /proc/1/attr/current)" = 'unifi (enforce)'
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
        docker("stop", "--time=300", recovered, timeout=320)
        print(
            "Actual installer interruption/refusal/verified pre-install recovery passed."
        )
    except BaseException as error:
        failure = error
        for name in (partial, refused, recovered):
            try:
                logs = docker("logs", "--tail=60", name, check=False, timeout=10)
                print(logs.stdout + logs.stderr)
            except (OSError, subprocess.TimeoutExpired) as diagnostic_error:
                print(
                    f"Recovery diagnostics unavailable: {type(diagnostic_error).__name__}"
                )
        raise
    finally:
        # Only this run's uniquely named, blank-data test artifacts are removed.
        cleanup_created(docker, containers, volumes, failure)


if __name__ == "__main__":
    if sys.argv[1:] == ["--interrupt-installer"]:
        interrupt_live_installer()
    elif sys.argv[1:2] == ["--compare-pathnames"]:
        assert len(sys.argv) == 4, "Archive and cold source paths are required"
        check_archive_pathnames(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        assert not sys.argv[1:], "Unknown recovery fixture arguments"
        main()
