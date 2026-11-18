"""The real-nginx test's container cleanup must not hide fixture failures."""

import contextlib
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

# Shared test fixtures live in the parent test directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Import the module, not the class: a module-level TestCase name would make
# discovery run the image-backed product test as part of the harness suite.
import test_ingress  # noqa: E402


class IngressCleanupTest(unittest.TestCase):
    def test_cleanup_errors_preserve_original_fixture_failure(self):
        for cleanup_error in (
            subprocess.TimeoutExpired("docker rm", 20),
            OSError("fixture"),
        ):
            with self.subTest(error=type(cleanup_error).__name__):
                case = test_ingress.IngressRuntimeTest()
                primary = subprocess.TimeoutExpired("docker run", 90)
                with (
                    mock.patch.object(case, "run_fixture", side_effect=primary) as run,
                    mock.patch(
                        "test_ingress.subprocess.run", side_effect=cleanup_error
                    ) as cleanup,
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(subprocess.TimeoutExpired) as caught,
                ):
                    case.test_real_nginx_with_synthetic_vendor_upstream()
                self.assertIs(caught.exception, primary)
                name = run.call_args.args[0]
                self.assertRegex(name, r"^unifi-ingress-test-[0-9a-f]{32}$")
                self.assertEqual(
                    cleanup.call_args.args[0], ["docker", "rm", "--force", name]
                )
                self.assertEqual(cleanup.call_args.kwargs["timeout"], 20)

    def test_cleanup_timeout_is_not_hidden_when_fixture_succeeded(self):
        case = test_ingress.IngressRuntimeTest()
        error = subprocess.TimeoutExpired("docker rm", 20)
        with (
            mock.patch.object(case, "run_fixture"),
            mock.patch("test_ingress.subprocess.run", side_effect=error),
            self.assertRaises(subprocess.TimeoutExpired) as caught,
        ):
            case.test_real_nginx_with_synthetic_vendor_upstream()
        self.assertIs(caught.exception, error)
