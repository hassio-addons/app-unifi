"""Mount-only adapter safety checks; mount commands are mocked here."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = (
    Path(__file__).resolve().parents[1] / "rootfs/usr/local/bin/unifi-os-legacy-layout"
)


class LegacyLayoutTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.storage = self.root / "storage"
        self.source = self.storage / "unifi/data"
        self.source.mkdir(parents=True)
        (self.source / "system.properties").write_text("unifi.db.name=ace\n")
        self.target = self.root / "vendor/data"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "mount-commands"
        (self.bin / "mount").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@" >> "$LAYOUT_TEST_MOUNT_LOG"\n'
        )
        (self.bin / "mountpoint").write_text("#!/bin/sh\nexit 1\n")
        (self.bin / "stat").write_text(
            '#!/usr/bin/env python3\nimport os,sys\ns=os.stat(sys.argv[-1]);print(f"{s.st_dev}:{s.st_ino}")\n'
        )
        for path in self.bin.iterdir():
            path.chmod(0o755)

    def run_layout(self):
        environment = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "LAYOUT_TEST_MOUNT_LOG": str(self.log),
        }
        return subprocess.run(
            [
                "bash",
                str(SCRIPT),
                str(self.source),
                str(self.target),
                str(self.storage),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

    def test_mount_command_not_symlink(self):
        before = (self.source / "system.properties").read_bytes()
        result = self.run_layout()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.target.is_dir())
        self.assertFalse(self.target.is_symlink())
        self.assertEqual(
            self.log.read_text().splitlines(),
            ["--bind", str(self.source.resolve()), str(self.target)],
        )
        self.assertEqual((self.source / "system.properties").read_bytes(), before)

    def test_unrelated_directory_not_overlaid(self):
        self.target.mkdir(parents=True)
        marker = self.target / "keep"
        marker.write_text("existing data")
        self.assertNotEqual(self.run_layout().returncode, 0)
        self.assertEqual(marker.read_text(), "existing data")
        self.assertFalse(self.log.exists())

    def test_target_symlink_not_replaced(self):
        self.target.parent.mkdir()
        self.target.symlink_to(self.source)
        self.assertNotEqual(self.run_layout().returncode, 0)
        self.assertTrue(self.target.is_symlink())
        self.assertFalse(self.log.exists())

    def test_missing_properties_not_mounted(self):
        (self.source / "system.properties").unlink()
        self.assertNotEqual(self.run_layout().returncode, 0)
        self.assertFalse(self.log.exists())

    def test_file_target_not_replaced(self):
        self.target.parent.mkdir()
        self.target.write_text("not a directory")
        self.assertNotEqual(self.run_layout().returncode, 0)
        self.assertEqual(self.target.read_text(), "not a directory")

    def test_known_storage_root_can_be_overlaid(self):
        self.target = self.storage
        self.assertEqual(self.run_layout().returncode, 0)
        self.assertTrue((self.source / "system.properties").is_file())


if __name__ == "__main__":
    unittest.main()
