"""Verify the exact-hash UI exception without checking vendor source into Git."""

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]


class SetupProgressScreenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        spec = importlib.util.spec_from_file_location(
            "setup_progress_screen",
            APP / "rootfs/usr/share/unifi-os/setup-progress-screen.py",
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.bundle = self.root / "main.synthetic.js"
        self.before = (
            b"/* synthetic fixture, not vendor code */ auth.user;"
            b"const ns=function(e){var s=(0,p.useSelector)(ae).restoreState;"
            b"var y=h&&i&&d;switch(e){"
            b'case dn:return m||["completed","inProgress","canceled"].includes(s);'
            b"}return memo(fn,[t,n,r,o,i,s,l,c,u,e,f,d])};var rs=fixture;"
            b"auth.user; s=(0,p.useSelector)(ae).restoreState;"
        )
        self.after = self.module.transform(self.before)
        self.module.ORIGINAL = hashlib.sha256(self.before).hexdigest()
        self.module.PATCHED = hashlib.sha256(self.after).hexdigest()
        self.bundle.write_bytes(self.before)
        self.bundle.chmod(0o640)

    def test_only_progress_changes_and_metadata_and_original_backup_survive(self):
        metadata = self.bundle.stat()
        backups = self.root / "backups"
        self.assertIn("patched;", self.module.apply(self.bundle, backups))
        self.assertEqual(self.bundle.read_bytes(), self.after)
        changed = self.bundle.stat()
        self.assertEqual(
            (changed.st_uid, changed.st_gid, changed.st_mode),
            (metadata.st_uid, metadata.st_gid, metadata.st_mode),
        )
        self.assertEqual(next(backups.iterdir()).read_bytes(), self.before)
        self.assertEqual(self.module.apply(self.bundle, backups), "already-patched")

    def test_unknown_hash_and_duplicate_conditions_are_not_modified(self):
        for content in (
            b"unknown source",
            self.before + self.module.AUTHENTICATED_IMPORT,
        ):
            self.bundle.write_bytes(content)
            with self.assertRaises(RuntimeError):
                self.module.apply(self.bundle, self.root / "backups")
            self.assertEqual(self.bundle.read_bytes(), content)

    def test_pinned_version_matches_the_image_installer(self):
        dockerfile = (APP / "Dockerfile").read_text()
        self.assertIn(
            f'echo "{self.module.SUPPORTED_VERSION}" > /opt/unifi-os/version', dockerfile
        )

    def test_progress_changes_are_function_scoped_and_dependency_is_present(self):
        prefix, suffix = self.before.split(b"const ns=function(e)", 1)
        outside = suffix.split(b";var rs=", 1)[1]
        self.assertTrue(self.after.startswith(prefix))
        self.assertTrue(self.after.endswith(outside))
        self.assertEqual(
            self.after.count(b"auth.user"), self.before.count(b"auth.user")
        )
        self.assertEqual(self.after.count(b"legacyMigrationRestoreType"), 3)
        self.assertIn(b"d,legacyMigrationRestoreType]", self.after)
        self.assertIn(self.module.AUTHENTICATED_IMPORT, self.after)

    def test_auth_gate_cannot_be_missing_or_duplicated_even_with_a_matching_fixture_hash(
        self,
    ):
        for replacement in (b"missing-auth", self.module.AUTHENTICATED_IMPORT * 2):
            content = self.before.replace(
                self.module.AUTHENTICATED_IMPORT, replacement, 1
            )
            self.bundle.write_bytes(content)
            self.module.ORIGINAL = hashlib.sha256(content).hexdigest()
            with self.assertRaises(RuntimeError):
                self.module.apply(self.bundle, self.root / "backups")
            self.assertEqual(self.bundle.read_bytes(), content)

    def test_duplicate_or_missing_progress_target_fails_even_with_fixture_hash(self):
        for old, _ in self.module.PROGRESS_REPLACEMENTS:
            for replacement in (b"missing-target", old + old):
                content = self.before.replace(old, replacement, 1)
                self.bundle.write_bytes(content)
                self.module.ORIGINAL = hashlib.sha256(content).hexdigest()
                with self.assertRaises(RuntimeError):
                    self.module.apply(self.bundle, self.root / "backups")
                self.assertEqual(self.bundle.read_bytes(), content)

    def test_untrustworthy_backup_is_never_overwritten(self):
        backups = self.root / "backups"
        backups.mkdir()
        backup = backups / f"{self.bundle.name}.{self.module.ORIGINAL}.original"
        backup.write_bytes(b"not the original")
        with self.assertRaises(RuntimeError):
            self.module.apply(self.bundle, backups)
        self.assertEqual(self.bundle.read_bytes(), self.before)
