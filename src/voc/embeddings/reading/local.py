"""Read a pinned personal export without models, ZenML, MLflow or cloud access."""

from ..shared.contracts import require_digest
from ..shared.profiles import resolve_profile
from ..shared.validation import load_release
from .contracts import validate_snapshot


def get_local_embeddings(
    *,
    dataset,
    directory,
    manifest_sha256,
    profile="minilm-verbatim-v1",
    allow_subset=False,
):
    """Verify the full source and exported bytes, then return aligned reviews/vectors."""
    require_digest(manifest_sha256, "manifest_sha256")
    identity, reviews = validate_snapshot(dataset, source="personal")
    bundle = load_release(directory, manifest_sha256=manifest_sha256)
    manifest = bundle.info
    if manifest["dataset"] != identity.as_dict():
        raise ValueError("Local embeddings belong to a different dataset identity")
    if manifest["profile"] != resolve_profile(profile).as_dict():
        raise ValueError("Local embeddings belong to a different profile")
    selection = manifest["selection"]
    if selection["kind"] != "full" and not allow_subset:
        raise ValueError("Subset embeddings require allow_subset=True")
    expected = reviews.iloc[: selection["row_count"]]
    for field in ("record_id", "model_text"):
        if bundle.reviews[field].tolist() != expected[field].tolist():
            raise ValueError("Local embeddings differ from source review order or text")
    return bundle
