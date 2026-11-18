"""Timeout cleanup must target only the container created by the test."""

import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

# Shared test fixtures live in the parent test directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docker_fixture import run_container  # noqa: E402


class DockerFixtureTest(unittest.TestCase):
    def test_timeout_still_removes_own_container_with_bounded_cleanup(self):
        timeout = subprocess.TimeoutExpired("docker", 30)
        with patch.dict(os.environ, {"UNIFI_MONGO_TEST_IMAGE": "test-image"}):
            with patch(
                "docker_fixture.subprocess.run",
                side_effect=[timeout, subprocess.CompletedProcess([], 1)],
            ) as run:
                with self.assertRaises(subprocess.TimeoutExpired) as raised:
                    run_container("sleep infinity", timeout=30)
        self.assertIs(raised.exception, timeout)
        start, cleanup = run.call_args_list
        command = start.args[0]
        name = command[command.index("--name") + 1]
        self.assertTrue(name.startswith("unifi-test-"))
        self.assertEqual(cleanup.args[0], ["docker", "rm", "--force", name])
        self.assertEqual(cleanup.kwargs["timeout"], 20)
        self.assertFalse(cleanup.kwargs["check"])

    @unittest.skipUnless(
        os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
    )
    def test_real_timeout_leaves_no_container(self):
        token = uuid4()
        name = f"unifi-test-{token.hex}"
        with patch("docker_fixture.uuid4", return_value=token):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_container("sleep infinity", timeout=2)
        result = subprocess.run(
            ["docker", "inspect", name],
            capture_output=True,
            check=False,
            timeout=20,
        )
        self.assertNotEqual(result.returncode, 0)
