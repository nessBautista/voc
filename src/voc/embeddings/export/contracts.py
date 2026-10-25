"""Export-specific input rules; release-format rules remain shared."""

from pathlib import Path

from ..shared.manifest import validate_manifest


def require_new_destination(directory, manifest) -> Path:
    """Validate metadata before filesystem effects and forbid replacing a release.

    Success of write_release is a complete, verified directory, not a new receipt
    object. Artifact content is validated in staging before it becomes visible.
    """
    validate_manifest(manifest)
    destination = Path(directory)
    if destination.exists():
        raise FileExistsError(f"Release directory already exists: {destination}")
    return destination
