"""Directory durability shared by release export and the reader cache."""

import os


def sync_directory(path):
    """Persist directory entries after creating or renaming local artifacts."""
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
