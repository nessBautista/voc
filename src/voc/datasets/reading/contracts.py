"""Reader port and existing personal/shared source selection policy."""

from typing import Protocol

import pandas as pd


class ArtifactReader(Protocol):
    def read(self, artifact_id: str) -> pd.DataFrame: ...


def selected_source(source=None):
    if source is None:
        from voc.storage import load_storage

        source = load_storage(destination="local").dataset_source
    # The public local spelling continues to select personal artifacts.
    if source == "local":
        source = "personal"
    if source not in ("personal", "s3"):
        raise ValueError("source must be personal or s3")
    return source
