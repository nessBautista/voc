"""Select local publication storage without loading Operator configuration."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    publications_dir: Path
    state_dir: Path

    @classmethod
    def from_env(cls):
        """Read only application settings; do not create directories or load secrets."""
        locations = []
        for name, default in (
            ("VOC_PUBLICATIONS_DIR", "/app/catalog"),
            ("VOC_APP_STATE_DIR", "/app/state"),
        ):
            value = os.environ.get(
                name,
                default,
            )
            # Deployment paths are local absolute paths, never storage URLs.
            if not value or "://" in value or not Path(value).is_absolute():
                raise ValueError(f"{name} must be an absolute local directory")
            locations.append(Path(value))
        return cls(*locations)
