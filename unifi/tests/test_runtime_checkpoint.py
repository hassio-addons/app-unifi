"""Opaque runtime backup preserves the metadata Supervisor cannot copy."""

import importlib.util
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

from docker_fixture import run_container

APP = Path(__file__).resolve().parents[1]


def member(name, kind=tarfile.REGTYPE, link="", uid=1001, gid=1001, mode=0o644):
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, link
    info.uid, info.gid, info.mode = uid, gid, mode
    return info


class ArchiveValidationTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "archive_validation",
            APP / "rootfs/usr/share/unifi-os/validate-runtime-archive.py",
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def validate(self, *members):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "archive.tar"
            with tarfile.open(path, "w") as archive:
                archive.addfile(member("home", tarfile.DIRTYPE, mode=0o755))
                archive.addfile(member("state", tarfile.DIRTYPE, mode=0o755))
                for info in members:
                    archive.addfile(info, io.BytesIO())
            self.module.validate(path)

    def test_nested_runtime_metadata_is_accepted(self):
        self.validate(
            # Image symlinks resolve inside the nested container.
            member("home/layer/usr/lib/alt", tarfile.SYMTYPE, "/etc/alternatives/x"),
            member("home/layer/sudo", mode=0o4755, uid=100000, gid=100000),
            member("home/layer/sudo-link", tarfile.LNKTYPE, "home/layer/sudo"),
            member("home/layer/whiteout", tarfile.CHRTYPE),
            member("home/layer/fifo", tarfile.FIFOTYPE, uid=101000),
        )

    def test_write_or_link_through_symlink_is_rejected(self):
        for second in (
            member("home/link/probe"),
            member("home/link/probe", tarfile.DIRTYPE),
            member("home/hard", tarfile.LNKTYPE, "home/link/passwd"),
        ):
            with self.subTest(name=second.name), self.assertRaises(ValueError):
                self.validate(member("home/link", tarfile.SYMTYPE, "/etc"), second)

    def test_root_set_id_and_root_fifo_are_rejected(self):
        for info in (
            member("home/su", mode=0o4755, uid=0),
            member("home/sg", mode=0o2755, gid=0),
            member("home/fifo", tarfile.FIFOTYPE, uid=0),
            member("home/disk", tarfile.BLKTYPE, uid=100000),
        ):
            with self.subTest(name=info.name), self.assertRaises(ValueError):
                self.validate(info)

    def test_path_escape_hardlinks_and_real_devices_are_rejected(self):
        module = self.module
        for name, kind, link, major in (
            ("../etc/passwd", tarfile.REGTYPE, "", 0),
            ("/etc/passwd", tarfile.REGTYPE, "", 0),
            ("home/link", tarfile.LNKTYPE, "../../etc/passwd", 0),
            ("home/device", tarfile.CHRTYPE, "", 1),
        ):
            with (
                self.subTest(name=name, kind=kind),
                tempfile.TemporaryDirectory() as temporary,
            ):
                path = Path(temporary) / "archive.tar"
                with tarfile.open(path, "w") as archive:
                    info = tarfile.TarInfo(name)
                    info.type, info.linkname, info.devmajor = kind, link, major
                    archive.addfile(info, io.BytesIO())
                with self.assertRaises(ValueError):
                    module.validate(path)


@unittest.skipUnless(
    os.environ.get("UNIFI_MONGO_TEST_IMAGE"), "requires built Linux image"
)
class RuntimeCheckpointTest(unittest.TestCase):
    def test_failed_full_gzip_check_cannot_publish_a_checkpoint(self):
        run_container(
            r"""
mkdir -p /run/checkpoint/home /run/checkpoint/state /run/stubs
printf 'state\n' > /run/checkpoint/state/server.conf
/usr/local/bin/unifi-os-runtime-storage snapshot /run/checkpoint
cp /run/checkpoint/runtime-checkpoint.tar.gz /run/previous.tar.gz
cp /run/checkpoint/runtime-checkpoint.tar.gz.sha256 /run/previous.sha256
cat > /run/stubs/gzip <<'STUB'
#!/bin/bash
if [[ "$1" == -t ]]; then exit 42; fi
exec /usr/bin/gzip "$@"
STUB
chmod 755 /run/stubs/gzip
export PATH=/run/stubs:$PATH
if /usr/local/bin/unifi-os-runtime-storage snapshot /run/checkpoint; then exit 1; else status=$?; fi
test "$status" = 42
test -f /run/checkpoint/runtime-checkpoint.incomplete
cmp /run/previous.tar.gz /run/checkpoint/runtime-checkpoint.tar.gz
cmp /run/previous.sha256 /run/checkpoint/runtime-checkpoint.tar.gz.sha256
test -z "$(find /run/checkpoint -maxdepth 1 -name '.runtime-checkpoint.*' -print)"
""",
            timeout=30,
        )

    def test_matching_sha_with_bad_gzip_trailer_is_rejected_before_extraction(self):
        run_container(
            r"""
mkdir /run/checkpoint
python3 - <<'PY'
import gzip
import io
import os
from pathlib import Path
import tarfile
stream = io.BytesIO()
with tarfile.open(fileobj=stream, mode='w') as archive:
    for name in ('home', 'state'):
        member = tarfile.TarInfo(name)
        member.type = tarfile.DIRTYPE
        archive.addfile(member)
    member = tarfile.TarInfo('state/server.conf')
    member.size = 5
    archive.addfile(member, io.BytesIO(b'state'))
# A tar reader can finish before the gzip trailer. Padding makes that boundary
# visible without a large dataset or any controller/vendor data.
compressed = bytearray(gzip.compress(stream.getvalue() + os.urandom(1024 * 1024), compresslevel=1, mtime=0))
compressed[-8] ^= 1
Path('/run/checkpoint/runtime-checkpoint.tar.gz').write_bytes(compressed)
PY
(cd /run/checkpoint && sha256sum runtime-checkpoint.tar.gz > runtime-checkpoint.tar.gz.sha256)
if /usr/bin/gzip -t /run/checkpoint/runtime-checkpoint.tar.gz; then exit 1; fi
if /usr/local/bin/unifi-os-runtime-storage restore /run/checkpoint; then exit 1; fi
test ! -e /run/checkpoint/home
test ! -e /run/checkpoint/state
test ! -e /run/checkpoint/runtime-restore.incomplete
""",
            timeout=30,
        )

    def test_snapshot_reaps_only_same_owner_reserved_temporary_files(self):
        run_container(
            r"""
mkdir -p /run/checkpoint/home /run/checkpoint/state
touch /run/checkpoint/.runtime-checkpoint.ABC123 /run/checkpoint/.runtime-checkpoint.ABC123.sha256
touch /run/checkpoint/.runtime-checkpoint.keep-long-name
ln -s /run/outside /run/checkpoint/.runtime-checkpoint.LINK12
/usr/local/bin/unifi-os-runtime-storage snapshot /run/checkpoint
test ! -e /run/checkpoint/.runtime-checkpoint.ABC123
test ! -e /run/checkpoint/.runtime-checkpoint.ABC123.sha256
test -f /run/checkpoint/.runtime-checkpoint.keep-long-name
test -L /run/checkpoint/.runtime-checkpoint.LINK12
""",
            timeout=30,
        )

    def test_archive_symlink_cannot_redirect_privileged_extraction(self):
        run_container(
            r"""
mkdir /run/checkpoint /run/outside
python3 - <<'PY'
import io
import tarfile
with tarfile.open('/run/checkpoint/runtime-checkpoint.tar.gz', 'w:gz') as archive:
    for name in ('home', 'state'):
        member = tarfile.TarInfo(name)
        member.type = tarfile.DIRTYPE
        archive.addfile(member)
    member = tarfile.TarInfo('home/link')
    member.type = tarfile.SYMTYPE
    member.linkname = '/run/outside'
    archive.addfile(member)
    member = tarfile.TarInfo('home/link/restore-probe')
    member.size = 5
    archive.addfile(member, io.BytesIO(b'probe'))
PY
(cd /run/checkpoint && sha256sum runtime-checkpoint.tar.gz > runtime-checkpoint.tar.gz.sha256)
if /usr/local/bin/unifi-os-runtime-storage restore /run/checkpoint; then exit 1; fi
test ! -e /run/outside/restore-probe
# Rejected by validation before extraction starts: nothing was extracted.
test ! -e /run/checkpoint/runtime-restore.incomplete
test ! -e /run/checkpoint/home
""",
            timeout=30,
        )

    def test_failed_extraction_cannot_be_mistaken_for_live_runtime(self):
        run_container(
            r"""
mkdir -p /run/checkpoint/home /run/checkpoint/state
printf 'state\n' > /run/checkpoint/state/server.conf
/usr/local/bin/unifi-os-runtime-storage snapshot /run/checkpoint
mv /run/checkpoint/home /run/saved-home
mv /run/checkpoint/state /run/saved-state
mkdir /run/stubs
cat > /run/stubs/tar <<'STUB'
#!/bin/bash
mkdir -p /run/checkpoint/home /run/checkpoint/state
touch /run/checkpoint/home/partial /run/checkpoint/state/partial
exit 43
STUB
chmod 755 /run/stubs/tar
export PATH=/run/stubs:$PATH
if /usr/local/bin/unifi-os-runtime-storage restore /run/checkpoint; then exit 1; else status=$?; fi
test "$status" = 43
test -f /run/checkpoint/runtime-restore.incomplete
if /usr/local/bin/unifi-os-runtime-storage restore /run/checkpoint; then exit 1; fi
if /usr/local/bin/unifi-os-runtime-storage snapshot /run/checkpoint; then exit 1; fi
""",
            timeout=30,
        )

    def test_roundtrip_preserves_uid_mode_hardlinks_and_no_live_overlay(self):
        run_container(
            r"""
root=/run/checkpoint-source
mkdir -p "$root/home/layers" "$root/state"
printf 'runtime identity\n' > "$root/state/server.conf"
printf 'synthetic executable\n' > "$root/home/layers/sudo"
chown 1001:1001 "$root/home/layers/sudo"
chmod 4755 "$root/home/layers/sudo"
ln "$root/home/layers/sudo" "$root/home/layers/hardlink"
ln -s /usr/lib/vendor "$root/home/layers/symlink"
mknod "$root/home/layers/whiteout" c 0 0
printf 'capability fixture\n' > "$root/home/layers/capability"
chmod 755 "$root/home/layers/capability"
setcap cap_net_bind_service=ep "$root/home/layers/capability"
python3 - "$root/home/layers/capability" <<'PY'
import os
import sys
os.setxattr(sys.argv[1], 'user.unifi-checkpoint-test', b'user metadata')
os.setxattr(sys.argv[1], 'trusted.unifi-checkpoint-test', b'trusted metadata')
PY
/usr/local/bin/unifi-os-runtime-storage snapshot "$root"
mkdir /run/checkpoint-restored
cp "$root/runtime-checkpoint.tar.gz" "$root/runtime-checkpoint.tar.gz.sha256" /run/checkpoint-restored/
/usr/local/bin/unifi-os-runtime-storage restore /run/checkpoint-restored
test "$(stat -c %u:%g:%a /run/checkpoint-restored/home/layers/sudo)" = 1001:1001:4755
test "$(stat -c %i /run/checkpoint-restored/home/layers/sudo)" = "$(stat -c %i /run/checkpoint-restored/home/layers/hardlink)"
test "$(readlink /run/checkpoint-restored/home/layers/symlink)" = /usr/lib/vendor
test -c /run/checkpoint-restored/home/layers/whiteout
test "$(stat -c %t:%T /run/checkpoint-restored/home/layers/whiteout)" = 0:0
python3 - "$root/home/layers/capability" /run/checkpoint-restored/home/layers/capability <<'PY'
import os
import sys
for name in ('user.unifi-checkpoint-test', 'trusted.unifi-checkpoint-test', 'security.capability'):
    assert os.getxattr(sys.argv[1], name) == os.getxattr(sys.argv[2], name), name
PY
printf 'new live state\n' > /run/checkpoint-restored/state/server.conf
/usr/local/bin/unifi-os-runtime-storage restore /run/checkpoint-restored
grep -q 'new live state' /run/checkpoint-restored/state/server.conf
""",
            timeout=30,
            # OCI root overlay itself rejects explicit whiteout mknod. Use a
            # real writable filesystem, as HAOS app storage does, not overlay.
            docker_options=(
                "--tmpfs=/run",
                "--cap-add=MKNOD",
                "--cap-add=SYS_ADMIN",
                "--security-opt=seccomp=unconfined",
            ),
        )

    def test_corrupt_or_incomplete_checkpoint_cannot_restore_stale_state(self):
        run_container(
            r"""
mkdir -p /run/checkpoint/home /run/checkpoint/state
printf 'state\n' > /run/checkpoint/state/server.conf
/usr/local/bin/unifi-os-runtime-storage snapshot /run/checkpoint
mv /run/checkpoint/home /run/saved-home
mv /run/checkpoint/state /run/saved-state
printf 'failed checkpoint\n' > /run/checkpoint/runtime-checkpoint.incomplete
if /usr/local/bin/unifi-os-runtime-storage restore /run/checkpoint; then exit 1; fi
rm /run/checkpoint/runtime-checkpoint.incomplete
printf 'corrupt' >> /run/checkpoint/runtime-checkpoint.tar.gz
if /usr/local/bin/unifi-os-runtime-storage restore /run/checkpoint; then exit 1; fi
test ! -e /run/checkpoint/state
""",
            timeout=30,
        )
