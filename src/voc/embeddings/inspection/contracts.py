"""Result contract owned by the embedding inspection feature."""

from typing import Literal, TypedDict


class EmbeddingInspection(TypedDict):
    """Metadata returned only after full release validation.

    This names the existing dictionary fields for callers and editor tooling;
    it does not replace runtime validation or copy the release manifest.
    """

    state: Literal["verified"]
    artifact_content_verified: Literal[True]
    release: dict
