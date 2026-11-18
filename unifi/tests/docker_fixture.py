"""Run only uniquely named disposable test containers, with bounded cleanup."""

import os
import subprocess
from uuid import uuid4


def run_container(script, *arguments, timeout, docker_options=()):
    name = f"unifi-test-{uuid4().hex}"
    try:
        return subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                *docker_options,
                "--network=none",
                "--entrypoint=bash",
                os.environ["UNIFI_MONGO_TEST_IMAGE"],
                "-euo",
                "pipefail",
                "-c",
                script,
                "--",
                *arguments,
            ],
            check=True,
            timeout=timeout,
        )
    finally:
        # --rm alone cannot clean up when a timeout kills only the Docker client.
        # The name belongs exclusively to this invocation, never another app.
        subprocess.run(
            ["docker", "rm", "--force", name],
            check=False,
            timeout=20,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
