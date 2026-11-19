"""Run a unittest discovery where every test is required: no skip is a pass.

CI uses this for the image-backed product run and for the harness self-tests.
A skipped, expected-failure or zero-test run fails; nothing is allow-listed.
"""

import argparse
import os
from pathlib import Path
import sys
import unittest

TESTS = Path(__file__).resolve().parent


def run(start, pattern="test_*.py", require_env=(), stream=None, environ=None):
    stream = stream or sys.stderr
    environ = os.environ if environ is None else environ
    missing = [name for name in require_env if not environ.get(name)]
    if missing:
        print(f"Required environment is unset: {', '.join(missing)}", file=stream)
        return 2
    start = Path(start).resolve()
    # Fixtures shared by product and harness tests live beside this runner.
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    loader = unittest.TestLoader()
    suite = loader.discover(str(start), pattern=pattern, top_level_dir=str(start))
    if loader.errors:
        for error in loader.errors:
            print(error, file=stream)
        return 1
    if suite.countTestCases() == 0:
        print(f"No tests discovered in {start}", file=stream)
        return 1
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    problems = []
    for test, reason in result.skipped:
        problems.append(f"SKIPPED (required): {test.id()}: {reason}")
    for test, _ in result.expectedFailures:
        problems.append(f"EXPECTED FAILURE (required to pass): {test.id()}")
    for problem in problems:
        print(problem, file=stream)
    print(
        f"{result.testsRun} run, {len(result.skipped)} skipped, "
        f"{len(result.failures)} failures, {len(result.errors)} errors",
        file=stream,
    )
    return 0 if result.wasSuccessful() and not problems else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start", help="discovery start directory")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument(
        "--require-env",
        action="append",
        default=[],
        metavar="NAME",
        help="fail before running unless this environment variable is set",
    )
    arguments = parser.parse_args(argv)
    return run(arguments.start, arguments.pattern, arguments.require_env)


if __name__ == "__main__":
    sys.exit(main())
