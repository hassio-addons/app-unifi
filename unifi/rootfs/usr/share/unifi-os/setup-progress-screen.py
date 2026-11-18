"""Pinned setup progress fix; retain the vendor's authenticated-import gate."""

import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

ORIGINAL = "a10bc169a51f7dbdad691b1fe59330905df9ec53afaae346b8a1ec4c733d9f14"
PATCHED = "356e6f3e459ae627b4c98bfe8bcd06212617b1de5743695c69070449539fd1f1"
SUPPORTED_VERSION = "5.1.42"
AUTHENTICATED_IMPORT = b"y=h&&i&&d"
PROGRESS_REPLACEMENTS = (
    (
        b"s=(0,p.useSelector)(ae).restoreState",
        b"s=(0,p.useSelector)(ae).restoreState,legacyMigrationRestoreType=(0,p.useSelector)(ae).restoreType",
    ),
    (
        b'case dn:return m||["completed","inProgress","canceled"].includes(s);',
        b'case dn:return m||"migration"===legacyMigrationRestoreType||["completed","inProgress","canceled"].includes(s);',
    ),
    (
        b"[t,n,r,o,i,s,l,c,u,e,f,d]",
        b"[t,n,r,o,i,s,l,c,u,e,f,d,legacyMigrationRestoreType]",
    ),
)
DIRECTORY = Path("/usr/share/unifi-core/app/node_modules/@ubnt/uhd-setup/dist")
BACKUPS = Path("/data/unifi-ha/setup-js-backups")


def transform(before):
    marker = b"const ns=function(e)"
    if before.count(marker) != 1:
        raise RuntimeError("No unique setup step filter; no changes made")
    start = before.index(marker)
    end = before.find(b";var rs=", start)
    if end < 0:
        raise RuntimeError("Unknown setup step filter boundary; no changes made")
    region = before[start:end]
    for old, new in PROGRESS_REPLACEMENTS:
        if region.count(old) != 1 or new in region:
            raise RuntimeError(
                "Unsupported/ambiguous setup step filter; no changes made"
            )
        region = region.replace(old, new, 1)
    if region.count(AUTHENTICATED_IMPORT) != 1:
        raise RuntimeError(
            "Authenticated import gate missing/ambiguous; no changes made"
        )
    # Every approved change belongs to ns; all bytes outside it stay unchanged.
    return before[:start] + region + before[end:]


def apply(path, backups):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Refusing a missing/symlink setup bundle")
    before = path.read_bytes()
    checksum = hashlib.sha256(before).hexdigest()
    if checksum == PATCHED:
        return "already-patched"
    if checksum != ORIGINAL:
        raise RuntimeError("Unsupported/ambiguous setup bundle; no changes made")
    after = transform(before)
    if hashlib.sha256(after).hexdigest() != PATCHED:
        raise RuntimeError("Unexpected replacement result; no changes made")
    metadata = path.stat()
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = backups / f"{path.name}.{checksum}.original"
    if backup.exists():
        if (
            backup.is_symlink()
            or hashlib.sha256(backup.read_bytes()).hexdigest() != checksum
        ):
            raise RuntimeError("Original backup is not trustworthy; refusing overwrite")
    else:
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(before)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copystat(path, backup)
        os.chown(backup, metadata.st_uid, metadata.st_gid)
    descriptor, name = tempfile.mkstemp(prefix=".ha-setup-progress-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copystat(path, temporary)
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
        if path.read_bytes() != before:
            raise RuntimeError(
                "Setup bundle changed during preparation; refusing replacement"
            )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    observed = path.stat()
    assert (observed.st_uid, observed.st_gid, observed.st_mode) == (
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_mode,
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == PATCHED
    return f"patched; original backup: {backup}"


if __name__ == "__main__":
    if sys.argv[1:] != [SUPPORTED_VERSION]:
        print(
            "WARNING: setup-progress UI patch skipped: it supports only UniFi OS "
            f"{SUPPORTED_VERSION}; the vendor setup screen is unchanged"
        )
        raise SystemExit(0)
    candidates = [
        path
        for path in DIRECTORY.glob("main.*.js")
        if path.is_file()
        and hashlib.sha256(path.read_bytes()).hexdigest()
        in (ORIGINAL, PATCHED)
    ]
    if len(candidates) != 1:
        raise SystemExit(
            "Setup-progress UI patch refused: no unique hash-matched "
            f"{SUPPORTED_VERSION} bundle"
        )
    print(apply(candidates[0], BACKUPS))
