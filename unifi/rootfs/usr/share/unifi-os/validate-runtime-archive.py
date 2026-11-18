"""Validate the checkpoint's fixed roots before privileged metadata extraction."""

from pathlib import PurePosixPath
import stat
import sys
import tarfile

ROOTS = ("home", "state")


def _inside_roots(path):
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and bool(path.parts)
        and path.parts[0] in ROOTS
    )


def _below_symlink(path, symlinks):
    return any(
        PurePosixPath(*path.parts[:depth]) in symlinks
        for depth in range(1, len(path.parts))
    )


def validate(name):
    symlinks = set()
    with tarfile.open(name, "r:*") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if not _inside_roots(path):
                raise ValueError("Checkpoint member escapes fixed runtime roots")
            # Symlinks inside container images may point anywhere (they are
            # resolved inside the nested container). No later member may be
            # written or hardlinked through one: that would leave the root.
            if _below_symlink(path, symlinks):
                raise ValueError("Checkpoint member is below a symlink")
            # Never forget a symlink, even if a later member reuses its name:
            # a real snapshot has unique names, so rejecting is conservative.
            if member.issym():
                symlinks.add(path)
            if member.islnk():
                target = PurePosixPath(member.linkname)
                if not _inside_roots(target) or _below_symlink(target, symlinks):
                    raise ValueError("Checkpoint hardlink escapes runtime roots")
            if member.ischr() and (member.devmajor, member.devminor) != (0, 0):
                raise ValueError("Unexpected non-whiteout character device")
            if member.isblk() or (member.isfifo() and member.uid == 0):
                raise ValueError(
                    "Checkpoint must not contain block devices or root FIFOs"
                )
            # Nested-runtime files are owned by mapped, unprivileged IDs. A
            # set-ID file owned by the outer root would grant outer privileges.
            if member.mode & (stat.S_ISUID | stat.S_ISGID) and (
                member.uid == 0 or member.gid == 0
            ):
                raise ValueError("Checkpoint contains a set-ID file owned by root")


if __name__ == "__main__":
    validate(sys.argv[1])
