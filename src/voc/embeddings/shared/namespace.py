"""Shared S3 namespace rules for embedding publishers, inspectors and readers."""

from voc.storage.settings import validate_prefixes

from .contracts import require_digest, require_uuid


def scope_prefix(storage, profile_id, dataset_release_id):
    require_digest(profile_id, "profile_id")
    require_uuid(dataset_release_id, "dataset_release_id")
    _, _, prefix = validate_prefixes(
        storage.shared_prefix, storage.members_prefix, storage.embeddings_prefix
    )
    return f"{prefix}/profiles/{profile_id}/datasets/{dataset_release_id}"
