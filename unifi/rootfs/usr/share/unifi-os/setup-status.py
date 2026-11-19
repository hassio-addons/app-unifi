"""Read one explicit vendor setting; never expose configuration or credentials."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

# Must equal the version pinned in the Dockerfile and setup-progress-screen.py
# (enforced by unit tests).
SUPPORTED_VERSION = "5.1.42"
VERSION = Path("/opt/unifi-os/version")
STATUS = Path("/run/unifi-os-setup-status/status.json")
WARNED = Path("/run/unifi-os-setup-status/version-warned")
# Written only after the vendor explicitly reported isSetup: true. The timer
# and the notification stop once it exists; it is never a migration receipt.
COMPLETE = Path("/var/lib/unifi-os-app/uos/setup-status/complete")
INNER = r"""
from pathlib import Path
import re
with Path("/data/unifi-core/config/settings.yaml").open("rb") as stream:
    body = stream.read(1024 * 1024 + 1)
assert len(body) <= 1024 * 1024
rows = re.findall(r"^isSetup:.*$", body.decode("utf-8"), re.M)
assert len(rows) == 1
match = re.fullmatch(r"isSetup: (true|false)", rows[0])
assert match is not None
print(match[1])
"""


def warn_unsupported(version):
    if WARNED.exists():
        return
    # <4> is journald's warning priority, so the app log shows it.
    print(
        f"<4>WARNING: installed UniFi OS version {version!r} is not the supported "
        f"{SUPPORTED_VERSION}; setup status is not read",
        file=sys.stderr,
        flush=True,
    )
    WARNED.touch()


def probe():
    version = VERSION.read_text().strip()
    if version != SUPPORTED_VERSION:
        warn_unsupported(version)
        return None
    result = subprocess.run(
        ["/usr/bin/podman", "exec", "-i", "uosserver", "/usr/bin/python3", "-"],
        input=INNER.encode(),
        capture_output=True,
        timeout=15,
    )
    # Errors may contain vendor paths/configuration. Do not print either stream.
    if result.returncode != 0:
        return None
    if result.stdout == b"true\n":
        return True
    if result.stdout == b"false\n":
        return False
    return None


def mark_complete():
    try:
        descriptor, name = tempfile.mkstemp(prefix=".complete-", dir=COMPLETE.parent)
    except OSError:
        return
    temporary = Path(name)
    try:
        os.close(descriptor)
        temporary.chmod(0o644)
        temporary.replace(COMPLETE)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    try:
        flag = probe()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        flag = None
    # Transient observation only: not a migration receipt or completion marker.
    if flag is True:
        mark_complete()
    data = {"isSetup": flag, "sampled_at": time.monotonic()}
    descriptor, name = tempfile.mkstemp(prefix=".status-", dir=STATUS.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(data, stream)
        temporary.chmod(0o644)
        temporary.replace(STATUS)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
