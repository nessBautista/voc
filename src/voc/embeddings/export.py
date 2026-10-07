"""Atomically assemble a portable release from already encoded, validated bytes."""

import errno
import os
import shutil
import tempfile
from pathlib import Path

from .contracts import canonical_json
from .manifest import validate_manifest
from .validation import load_release


def sync_directory(path):
    """Persist directory entries after creating or renaming local artifacts."""
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def write_release(directory, manifest, *, vectors_path, reviews_path, expected_reviews):
    """Create a new immutable directory; never overwrite an existing release.

    Copying the existing NPY/parquet bytes preserves their exact representation and
    checksums. The shared validator checks stored dtype/order without coercion.
    Only the completed-run adapter may establish actual producer provenance.
    """
    validate_manifest(manifest)
    destination = Path(directory)
    if destination.exists():
        raise FileExistsError(f"Release directory already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".release-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        for source, name in (
            (vectors_path, "vectors.npy"),
            (reviews_path, "reviews.parquet"),
        ):
            key = "vectors" if name == "vectors.npy" else "reviews"
            if Path(source).stat().st_size != manifest["artifacts"][key]["size_bytes"]:
                raise ValueError(f"Source artifact size differs: {name}")
            path = staging / name
            shutil.copyfile(source, path)
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        with (staging / "manifest.json").open("wb") as stream:
            stream.write(canonical_json(manifest))
            stream.flush()
            os.fsync(stream.fileno())
        checked = load_release(staging, expected_reviews=expected_reviews)
        # Release the memory map before moving the directory, including on Windows.
        del checked
        if destination.exists():
            raise FileExistsError(f"Release directory already exists: {destination}")
        sync_directory(staging)
        try:
            staging.rename(destination)
        except OSError as error:
            if error.errno in (errno.EEXIST, errno.ENOTEMPTY):
                raise FileExistsError(
                    f"Release directory already exists: {destination}"
                ) from error
            raise
        sync_directory(destination.parent)
