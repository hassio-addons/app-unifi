# Testing the UniFi OS Server app

This document is for contributors and maintainers. User documentation lives in
[`../DOCS.md`](../DOCS.md).

All tests use disposable containers and volumes. None of them touch a Home
Assistant installation, real UniFi devices or credentials. Never point them at
real controller data.

## Layout

- `test_*.py`: unit and container tests. Most of them need the built app image.
- `harness/`: tests of the test tooling itself (required-test runner,
  fixtures, release version guard).
- `*.cjs`: Node.js fixtures for the ingress browser adapter, cookie filter and
  login redirect, run from the Python tests.
- `test_bind_layout.sh`: checks the legacy data bind mounts in a real
  container.
- `check_lifecycle.py`, `check_install_recovery.py`, `check_base_upgrade.py`:
  end-to-end checks that install the real UniFi OS Server in a container.

## Requirements

- A Linux host with Docker, on the architecture you test (`amd64` or
  `arm64`). Build and run natively; a cross-built image is not enough.
- Python 3 with PyYAML.
- Node.js (CI uses version 24).
- `apparmor_parser`, for the AppArmor profile tests.
- For the end-to-end checks: AppArmor enabled, cgroup v2, and enough free disk
  space and memory for a UniFi OS Server installation.
- ShellCheck and hadolint for the static checks.

## Running the tests

Build the image for your architecture:

```sh
docker build --platform linux/arm64 -t unifi-os-candidate:test unifi
```

Run the unit and container tests, then the harness tests. The runner fails on
any skipped test, so a missing tool or image is reported instead of silently
passing:

```sh
UNIFI_MONGO_TEST_IMAGE=unifi-os-candidate:test \
  UNIFI_INGRESS_TEST_IMAGE=unifi-os-candidate:test \
  python3 unifi/tests/run_required_tests.py unifi/tests \
  --require-env UNIFI_MONGO_TEST_IMAGE \
  --require-env UNIFI_INGRESS_TEST_IMAGE

UNIFI_MONGO_TEST_IMAGE=unifi-os-candidate:test \
  python3 unifi/tests/run_required_tests.py unifi/tests/harness \
  --require-env UNIFI_MONGO_TEST_IMAGE
```

For a quick check without an image, `python3 -m unittest discover -s
unifi/tests -p 'test_*.py'` runs the tests that need no image and skips the
rest. That is not a full pass.

Check the bind mount layout:

```sh
docker run --rm --network=none --cap-add=SYS_ADMIN \
  --security-opt apparmor=unconfined --entrypoint=bash \
  -v "${PWD}/unifi/tests/test_bind_layout.sh:/run/test-layout:ro" \
  unifi-os-candidate:test /run/test-layout
```

Static checks:

```sh
shellcheck unifi/rootfs/usr/local/bin/unifi-os-* \
  unifi/rootfs/usr/lib/unifi/bin/mongod unifi/build/* unifi/tests/*.sh
python3 -W error -m py_compile unifi/tests/*.py \
  unifi/tests/harness/*.py
hadolint unifi/Dockerfile
```

### End-to-end checks

These load the app's AppArmor profile on the Docker host and install UniFi OS
Server in uniquely named containers and volumes, which they remove afterwards.
Each can take up to about an hour.

```sh
sudo apparmor_parser --replace unifi/apparmor.txt
UNIFI_OS_LIFECYCLE_TEST_IMAGE=unifi-os-candidate:test \
  python3 unifi/tests/check_lifecycle.py
UNIFI_OS_RECOVERY_TEST_IMAGE=unifi-os-candidate:test \
  python3 unifi/tests/check_install_recovery.py
UNIFI_OS_UPGRADE_TEST_IMAGE=unifi-os-candidate:test \
  python3 unifi/tests/check_base_upgrade.py
```

- `check_lifecycle.py` installs UniFi OS on blank data under the enforcing
  profile, then tests restart and container recreation. It then copies the
  data the way a Home Assistant backup does (without the raw runtime
  directories), restores it into a new volume and checks that the app starts
  from the checkpoint unchanged.
- `check_install_recovery.py` interrupts the real installer, checks that the
  next start refuses the partial installation, and recovers from a cold copy of
  the data taken before the installation.
- `check_base_upgrade.py` starts the previous release (`5.3.2`) on a blank
  volume so that it creates a real MongoDB 3.6 database, then starts the new
  image on the same volume and checks the upgrade to MongoDB 4.4 and the UniFi
  OS installation.

## Continuous integration

`.github/workflows/unifi-os-candidate.yaml` runs on pull requests that change
`unifi/**` or the workflow itself, on manual dispatch, and from the deploy
workflow. Every job runs natively on both `amd64` and `arm64` runners:

- **static**: ShellCheck and Python compilation of the test and CI scripts.
- **build**: builds the image and passes it to the other jobs.
- **unit**: the required unit and container tests, the bind mount test and the
  harness tests, as above.
- **lifecycle**, **install-recovery** and **base-upgrade**: the three
  end-to-end checks.

`.github/workflows/deploy.yaml` runs on pushes to `main` and on published
releases. It only publishes images when both of these pass:

- the shared Home Assistant Community Apps CI (linters and image build),
- the UniFi OS candidate checks above.

## Qualifying a new UniFi OS Server version

Several parts of the app are pinned to UniFi OS Server 5.1.42. When moving to a
new version, check and update each of them:

1. **Installer**: the download URLs and SHA-256 checksums for both
   architectures, and the version written to `/opt/unifi-os/version`, in
   `unifi/Dockerfile`. Ubiquiti publishes no signature; take the checksums from
   the official downloads.
1. **Supported version**: `SUPPORTED_VERSION` in
   `rootfs/usr/share/unifi-os/setup-progress-screen.py` and
   `rootfs/usr/share/unifi-os/setup-status.py`. The tests require both to match
   the Dockerfile.
1. **Setup progress screen**: the `ORIGINAL` and `PATCHED` SHA-256 values and
   the replacement targets in `setup-progress-screen.py`. Re-derive them from
   the new setup bundle, or drop the change if the vendor fixed the progress
   step. Unknown bundles are refused, never patched.
1. **Setup status**: the location and format of the `isSetup` flag that
   `setup-status.py` reads inside UniFi OS.
1. **Ingress rewrites**: the pinned `sub_filter` literals in
   `rootfs/etc/unifi-os/ingress.conf` (login location, webpack public path,
   release-channel cookie writer) and the matching literals in
   `rootfs/usr/local/bin/unifi-os-vendor-selfcheck`.
1. **Vendor self-check**: on a real installation, the app log must report that
   the vendor portal code matches the pinned ingress rewrites, and no
   "Unsupported UniFi OS version" or "Home Assistant Web UI may not work"
   notification may appear.
1. **Vendor layout**: ports in `unifi/config.yaml` and the port aliases, the
   updater sandbox in
   `rootfs/etc/systemd/system/uosserver-updater.service.d/home-assistant.conf`
   and the paths checked by `unifi-os-exec-guard`. UniFi OS updates started
   from inside the console must stay blocked.
1. **Upgrade path**: an installation of the previous pinned version must update
   through the vendor installer, and all CI jobs must pass, including
   `base-upgrade` from the `5.3.2` release.

Then test by hand on a disposable Home Assistant installation that is not
connected to real devices:

- a new installation and setup,
- an update from the previous release, with the wizard's import (Ubiquiti
  sign-in) and with a `.unf` restore (local account),
- the console inside Home Assistant over HTTPS, the HTTP redirect page, and
  `ingress_allow_insecure_http`,
- a Home Assistant backup and restore of the app, and rolling back by
  restoring a backup made before the update.
