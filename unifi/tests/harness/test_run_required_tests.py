"""The required-test runner must fail on skips, empty runs and missing setup."""

from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

RUNNER = Path(__file__).resolve().parents[1] / "run_required_tests.py"


class RequiredTestRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, body):
        (self.root / "test_sample.py").write_text(
            "import unittest\n\n" + textwrap.dedent(body)
        )

    def run_runner(self, *arguments, env=None):
        return subprocess.run(
            [sys.executable, str(RUNNER), str(self.root), *arguments],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )

    def test_passing_suite_succeeds(self):
        self.write(
            """
            class Sample(unittest.TestCase):
                def test_pass(self):
                    pass
            """
        )
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 run, 0 skipped", result.stderr)

    def test_any_skip_fails(self):
        for body in (
            """
            @unittest.skip("image unavailable")
            class Sample(unittest.TestCase):
                def test_skipped(self):
                    pass
            """,
            """
            class Sample(unittest.TestCase):
                def test_pass(self):
                    pass

                def test_skipped(self):
                    with self.subTest(case=1):
                        self.skipTest("requires node")
            """,
        ):
            with self.subTest(body=body):
                self.write(body)
                result = self.run_runner()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("SKIPPED (required)", result.stderr)

    def test_expected_failure_fails(self):
        self.write(
            """
            class Sample(unittest.TestCase):
                @unittest.expectedFailure
                def test_hidden(self):
                    self.fail()
            """
        )
        result = self.run_runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("EXPECTED FAILURE", result.stderr)

    def test_failure_fails(self):
        self.write(
            """
            class Sample(unittest.TestCase):
                def test_fail(self):
                    self.fail("broken")
            """
        )
        self.assertEqual(self.run_runner().returncode, 1)

    def test_import_error_fails(self):
        (self.root / "test_broken.py").write_text("import missing_module_xyz\n")
        self.assertEqual(self.run_runner().returncode, 1)

    def test_empty_discovery_fails(self):
        result = self.run_runner()
        self.assertEqual(result.returncode, 1)
        self.assertIn("No tests discovered", result.stderr)

    def test_missing_required_environment_fails_before_running(self):
        self.write(
            """
            class Sample(unittest.TestCase):
                def test_pass(self):
                    pass
            """
        )
        result = self.run_runner(
            "--require-env", "UNIFI_REQUIRED_TEST_UNSET", env={"PATH": ""}
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("UNIFI_REQUIRED_TEST_UNSET", result.stderr)
        self.assertNotIn("test_pass", result.stderr)
