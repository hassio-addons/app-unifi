"""The synthetic nginx fixture must await graceful worker replacement."""

from pathlib import Path
import signal
import sys
import unittest
from unittest import mock

# Shared test fixtures live in the parent test directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ingress_fixture  # noqa: E402


class IngressReloadTest(unittest.TestCase):
    def setUp(self):
        self.nginx = mock.Mock(pid=100)
        self.nginx.poll.return_value = None

    def test_children_are_read_from_the_master_process(self):
        with mock.patch.object(
            ingress_fixture.Path, "read_text", return_value="101 102 "
        ) as read:
            self.assertEqual(ingress_fixture.nginx_children(self.nginx), {"101", "102"})
        read.assert_called_once_with()

    def test_reload_waits_for_all_old_workers_not_only_new_worker_start(self):
        with (
            mock.patch.object(
                ingress_fixture,
                "nginx_children",
                side_effect=[{"101", "102"}, {"101", "103"}, {"103"}],
            ) as children,
            mock.patch.object(ingress_fixture.time, "sleep") as sleep,
        ):
            ingress_fixture.reload_nginx(self.nginx)
        self.nginx.send_signal.assert_called_once_with(signal.SIGHUP)
        self.assertEqual(children.call_count, 3)
        sleep.assert_called_once_with(0.01)

    def test_empty_worker_set_is_not_success(self):
        with (
            mock.patch.object(
                ingress_fixture, "nginx_children", side_effect=[{"101"}, set(), {"102"}]
            ),
            mock.patch.object(ingress_fixture.time, "sleep") as sleep,
        ):
            ingress_fixture.reload_nginx(self.nginx)
        sleep.assert_called_once_with(0.01)

    def test_rejected_reload_fails_after_bounded_wait(self):
        with (
            mock.patch.object(ingress_fixture, "nginx_children", return_value={"101"}),
            mock.patch.object(ingress_fixture.time, "monotonic", side_effect=[0, 0, 6]),
            mock.patch.object(ingress_fixture.time, "sleep"),
            self.assertRaisesRegex(AssertionError, "previous worker generation"),
        ):
            ingress_fixture.reload_nginx(self.nginx)

    def test_master_exit_is_not_success(self):
        self.nginx.poll.return_value = 1
        with (
            mock.patch.object(ingress_fixture, "nginx_children", return_value={"101"}),
            self.assertRaisesRegex(AssertionError, "master exited"),
        ):
            ingress_fixture.reload_nginx(self.nginx)
