"""Read-only inspection workflow over shared dataset and embedding readers."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..shared.contracts import EmbeddingBundle, require_uuid
from ..shared.profiles import resolve_profile
from .contracts import EmbeddingInspection

if TYPE_CHECKING:
    from voc.models import Snapshot


def inspect_embeddings(
    *,
    dataset_version: str,
    version: str = "latest",
    profile: str = "minilm-verbatim-v1",
    source: str = "s3",
    allow_subset: bool = False,
    dataset_reader: Callable[..., "Snapshot"] | None = None,
    embedding_reader: Callable[..., EmbeddingBundle] | None = None,
) -> EmbeddingInspection:
    """Read and fully verify a release, returning metadata rather than vectors.

    Readers default to voc.collector.get_dataset and voc.get_embeddings. Injected
    callables must implement those keyword contracts and retain their validation:
    dataset_reader(stage, version, source) returns a verified Snapshot;
    embedding_reader(dataset, profile, version, source, allow_subset) returns a
    fully verified EmbeddingBundle. The dataset is resolved once and passed on.

    Validate options before invoking either reader. Existing readers may download
    and populate caches; pinned cached reads can work offline, while latest must
    resolve online. No inference runs. Reader failures propagate to the caller;
    output formatting, stream redirection and exit codes belong to entry points.
    """
    if dataset_version != "latest":
        require_uuid(dataset_version, "dataset_version")
    if version != "latest":
        require_uuid(version, "embedding_release_id")
    resolve_profile(profile)
    if source != "s3":
        raise ValueError("Embedding reads currently require source='s3'")
    if type(allow_subset) is not bool:
        raise ValueError("allow_subset must be a boolean")

    if dataset_reader is None:
        from voc.collector import get_dataset

        dataset_reader = get_dataset
    if embedding_reader is None:
        from voc import get_embeddings

        embedding_reader = get_embeddings

    dataset = dataset_reader(stage="prepared", version=dataset_version, source=source)
    bundle = embedding_reader(
        dataset=dataset,
        profile=profile,
        version=version,
        source=source,
        allow_subset=allow_subset,
    )
    return {
        "state": "verified",
        "artifact_content_verified": True,
        "release": bundle.info,
    }
