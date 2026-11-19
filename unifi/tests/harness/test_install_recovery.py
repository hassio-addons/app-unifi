"""A generic startup failure cannot pass the installer-recovery refusal gate."""

import unittest
import subprocess
import signal
from pathlib import Path
import sys
import tarfile
import tempfile
from unittest import mock

# Shared test fixtures live in the parent test directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_install_recovery as recovery  # noqa: E402
from check_install_recovery import check_partial_refusal, cleanup_created  # noqa: E402

REFUSAL = (
    "UniFi OS installation guard refused: partial/existing installation without receipt;"
    " inspect/preserve state before recovery"
)


class InstallRecoveryTest(unittest.TestCase):
    def test_explicit_partial_state_refusal_is_accepted(self):
        check_partial_refusal({"Running": False, "ExitCode": 1}, REFUSAL)

    def test_still_running_partial_state_is_not_accepted(self):
        with self.assertRaisesRegex(AssertionError, "still running"):
            check_partial_refusal({"Running": True, "ExitCode": 1}, REFUSAL)

    def test_clean_exit_is_not_fail_closed_refusal(self):
        with self.assertRaisesRegex(AssertionError, "unexpectedly accepted"):
            check_partial_refusal({"Running": False, "ExitCode": 0}, REFUSAL)

    def test_an_unrelated_startup_failure_does_not_pass(self):
        for logs in (
            "",
            "Disk preflight failed",
            "UniFi OS installation guard refused: downgrade",
        ):
            with (
                self.subTest(logs=logs),
                self.assertRaisesRegex(AssertionError, "another reason"),
            ):
                check_partial_refusal({"Running": False, "ExitCode": 1}, logs)

    def test_cleanup_failure_does_not_hide_primary_error_or_skip_other_artifacts(self):
        docker = mock.Mock(
            side_effect=[
                subprocess.TimeoutExpired("docker rm", 30),
                subprocess.CompletedProcess(["docker"], 0, "", ""),
                subprocess.CompletedProcess(["docker"], 0, "", ""),
            ]
        )
        cleanup_created(
            docker,
            ["own-container", "own-helper"],
            ["own-volume"],
            RuntimeError("primary"),
        )
        self.assertEqual(
            docker.call_args_list,
            [
                mock.call("rm", "--force", "own-container", check=False, timeout=30),
                mock.call("rm", "--force", "own-helper", check=False, timeout=30),
                mock.call("volume", "rm", "own-volume", check=False, timeout=30),
            ],
        )

    def test_cleanup_failure_is_reported_when_rehearsal_succeeded(self):
        error = OSError("cleanup")
        docker = mock.Mock(side_effect=error)
        with self.assertRaises(OSError) as caught:
            cleanup_created(docker, ["own-container"], ["own-volume"], None)
        self.assertIs(caught.exception, error)
        self.assertEqual(docker.call_count, 2)

    def test_nonzero_cleanup_retains_primary_failure_and_continues(self):
        docker = mock.Mock(
            side_effect=[
                subprocess.CompletedProcess(["docker", "rm"], 1, "", "removal failed"),
                subprocess.CompletedProcess(
                    ["docker", "container", "ls"], 0, "own-container\n", ""
                ),
                subprocess.CompletedProcess(["docker", "volume", "rm"], 0, "", ""),
            ]
        )
        cleanup_created(
            docker, ["own-container"], ["own-volume"], RuntimeError("primary")
        )
        self.assertEqual(docker.call_count, 3)

    def test_nonzero_cleanup_fails_an_otherwise_successful_rehearsal(self):
        docker = mock.Mock(
            side_effect=[
                subprocess.CompletedProcess(["docker", "rm"], 1, "", "removal failed"),
                subprocess.CompletedProcess(
                    ["docker", "container", "ls"], 0, "own-container\n", ""
                ),
            ]
        )
        with self.assertRaises(subprocess.CalledProcessError):
            cleanup_created(docker, ["own-container"], [], None)

    def test_only_confirmed_absence_ignores_nonzero_cleanup(self):
        for listing in [
            subprocess.CompletedProcess(["docker", "container", "ls"], 0, "", ""),
            subprocess.CompletedProcess(
                ["docker", "container", "ls"], 1, "", "daemon error"
            ),
        ]:
            docker = mock.Mock(
                side_effect=[
                    subprocess.CompletedProcess(
                        ["docker", "rm"], 1, "", "removal failed"
                    ),
                    listing,
                ]
            )
            with self.subTest(exit=listing.returncode):
                if listing.returncode == 0:
                    cleanup_created(docker, ["own-container"], [], None)
                else:
                    with self.assertRaises(subprocess.CalledProcessError):
                        cleanup_created(docker, ["own-container"], [], None)


class LiveInstallerInterruptionTest(unittest.TestCase):
    def test_only_the_pinned_executable_in_the_bootstrap_cgroup_is_targeted(self):
        for matches_binary, cgroup in [
            (True, "/system.slice/unifi-os-bootstrap.service"),
            (False, "/system.slice/unifi-os-bootstrap.service"),
            (True, "/system.slice/unrelated.service"),
        ]:

            def stat(path):
                inode = (
                    2 if str(path) == "/opt/unifi-os/installer" or matches_binary else 3
                )
                return mock.Mock(st_dev=1, st_ino=inode)

            with (
                self.subTest(binary=matches_binary, cgroup=cgroup),
                mock.patch.dict(recovery.os.environ, {"container": "docker"}),
                mock.patch.object(
                    recovery.Path, "stat", autospec=True, side_effect=stat
                ),
                mock.patch.object(
                    recovery.Path, "iterdir", return_value=[recovery.Path("/proc/101")]
                ),
                mock.patch.object(
                    recovery.Path, "read_text", return_value="0::" + cgroup
                ),
                mock.patch.object(recovery, "terminate_live_installer") as terminate,
            ):
                if matches_binary and cgroup.endswith("/unifi-os-bootstrap.service"):
                    recovery.interrupt_live_installer()
                    terminate.assert_called_once_with(101, (1, 2))
                else:
                    with self.assertRaisesRegex(AssertionError, "unique live vendor"):
                        recovery.interrupt_live_installer()
                    terminate.assert_not_called()

    def test_stop_and_kill_target_the_same_live_pidfd(self):
        poll = mock.Mock()
        poll.poll.side_effect = [[], [], [(20, 1)]]
        with (
            mock.patch.object(recovery.os, "pidfd_open", return_value=20, create=True),
            mock.patch.object(
                recovery.signal, "pidfd_send_signal", create=True
            ) as send,
            mock.patch.object(recovery.select, "poll", return_value=poll),
            mock.patch.object(
                recovery.Path, "stat", return_value=mock.Mock(st_dev=1, st_ino=2)
            ),
            mock.patch.object(
                recovery.Path, "read_text", return_value="State:\tT (stopped)\n"
            ),
            mock.patch.object(recovery.Path, "exists", return_value=False),
            mock.patch.object(recovery.os, "close") as close,
        ):
            recovery.terminate_live_installer(100, (1, 2))
        self.assertEqual(
            send.call_args_list,
            [
                mock.call(20, signal.SIGSTOP),
                mock.call(20, signal.SIGKILL),
            ],
        )
        close.assert_called_once_with(20)

    def test_completed_installer_is_not_signaled_or_counted_as_interrupted(self):
        poll = mock.Mock()
        poll.poll.return_value = [(20, 1)]
        with (
            mock.patch.object(recovery.os, "pidfd_open", return_value=20, create=True),
            mock.patch.object(
                recovery.signal, "pidfd_send_signal", create=True
            ) as send,
            mock.patch.object(recovery.select, "poll", return_value=poll),
            mock.patch.object(recovery.os, "close"),
            self.assertRaisesRegex(AssertionError, "already exited"),
        ):
            recovery.terminate_live_installer(100, (1, 2))
        send.assert_not_called()

    def test_completion_between_stop_request_and_stopped_state_is_rejected(self):
        poll = mock.Mock()
        poll.poll.side_effect = [[], [(20, 1)]]
        with (
            mock.patch.object(recovery.os, "pidfd_open", return_value=20, create=True),
            mock.patch.object(
                recovery.signal, "pidfd_send_signal", create=True
            ) as send,
            mock.patch.object(recovery.select, "poll", return_value=poll),
            mock.patch.object(
                recovery.Path, "stat", return_value=mock.Mock(st_dev=1, st_ino=2)
            ),
            mock.patch.object(recovery.os, "close"),
            self.assertRaisesRegex(AssertionError, "before it could be stopped"),
        ):
            recovery.terminate_live_installer(100, (1, 2))
        self.assertNotIn(mock.call(20, signal.SIGKILL), send.call_args_list)


class ColdArchivePathnamesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "source"
        self.root.mkdir()
        (self.root / "uos/bootstrap").mkdir(parents=True)
        (self.root / ".hidden").write_text("sentinel")
        (self.root / "uos/link").symlink_to("bootstrap", target_is_directory=True)
        self.archive = Path(self.temporary.name) / "cold.tar"
        with tarfile.open(self.archive, "w") as stream:
            stream.add(self.root, arcname=".")

    def test_matching_hidden_paths_and_symlinks_are_accepted(self):
        recovery.check_archive_pathnames(self.archive, self.root)

    def test_added_receipt_is_rejected(self):
        (self.root / "uos/bootstrap/installed-version").write_text("5.1.42")
        with self.assertRaisesRegex(AssertionError, "1 additions"):
            recovery.check_archive_pathnames(self.archive, self.root)

    def test_other_added_paths_are_rejected(self):
        (self.root / "uos/new-file").write_text("unexpected")
        with self.assertRaisesRegex(AssertionError, "1 additions"):
            recovery.check_archive_pathnames(self.archive, self.root)

    def test_missing_paths_are_rejected(self):
        (self.root / ".hidden").unlink()
        with self.assertRaisesRegex(AssertionError, "1 missing"):
            recovery.check_archive_pathnames(self.archive, self.root)
