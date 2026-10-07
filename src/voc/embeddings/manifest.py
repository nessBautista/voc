"""Validate release metadata, without inference, artifact I/O or S3 mutation.

Exporters and readers must separately verify file checksums, shapes and mappings. A
valid manifest alone is never proof that artifacts exist or a run has completed.
"""

import re
from datetime import datetime

from .contracts import (
    SCHEMA_VERSION,
    DatasetIdentity,
    InputSelection,
    canonical_json,
    encoder_identity,
    feature_identity,
    require_digest,
    require_fields,
    require_key,
    require_positive_int,
    require_uuid,
)
from .profiles import resolve_profile

MAX_JSON_BYTES = 1024**2
MAX_FILE_BYTES = 1024**3


def validate_manifest(manifest):
    require_fields(
        manifest,
        {
            "schema_version",
            "embedding_release_id",
            "dataset",
            "selection",
            "feature",
            "profile",
            "encoder",
            "coverage",
            "artifacts",
            "provenance",
        },
        "manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported embedding manifest schema")
    if len(canonical_json(manifest)) > MAX_JSON_BYTES:
        raise ValueError("Embedding manifest exceeds metadata size limit")
    release = manifest["embedding_release_id"]
    require_uuid(release, "embedding_release_id")
    require_fields(manifest["dataset"], DatasetIdentity.__dataclass_fields__, "dataset")
    dataset = DatasetIdentity(**manifest["dataset"])
    selected = manifest["selection"]
    require_fields(
        selected,
        {"kind", "limit", "row_count", "rows_sha256", "selection_id"},
        "selection",
    )
    selection = InputSelection(
        dataset, selected["row_count"], selected["rows_sha256"], selected["limit"]
    )
    if selected != selection.as_dict():
        raise ValueError("Selection kind or identity differs from its descriptor")

    profile_meta = manifest["profile"]
    require_fields(profile_meta, {"name", "profile_id", "config"}, "profile")
    profile = resolve_profile(profile_meta["name"])
    # Comparing canonical bytes also rejects bool/int substitutions in nested JSON.
    if canonical_json(profile_meta) != canonical_json(profile.as_dict()):
        raise ValueError("Profile name/configuration/identity mismatch")
    feature = manifest["feature"]
    require_fields(feature, {"policy", "rows_sha256", "feature_id"}, "feature")
    if feature["policy"] != profile.preparation_policy:
        raise ValueError("Feature preparation policy differs from profile")
    if feature["feature_id"] != feature_identity(
        selection.selection_id, feature["policy"], feature["rows_sha256"]
    ):
        raise ValueError("Feature identity mismatch")
    encoder = manifest["encoder"]
    require_fields(encoder, {"encoder_id", "descriptor"}, "encoder")
    if encoder_identity(encoder["descriptor"]) != encoder["encoder_id"]:
        raise ValueError("Encoder identity mismatch")
    if encoder["descriptor"]["profile_id"] != profile.profile_id:
        raise ValueError("Encoder profile mismatch")

    counts = manifest["coverage"]
    require_fields(counts, {"source_rows", "selected_rows", "unique_texts"}, "coverage")
    for field, value in counts.items():
        require_positive_int(value, "coverage." + field)
    if (
        counts["source_rows"] != dataset.row_count
        or counts["selected_rows"] != selection.row_count
        or counts["unique_texts"] > selection.row_count
    ):
        raise ValueError("Coverage counts do not reconcile")

    artifacts = manifest["artifacts"]
    require_fields(artifacts, {"vectors", "reviews"}, "artifacts")
    for name, filename in (("vectors", "vectors.npy"), ("reviews", "reviews.parquet")):
        artifact = artifacts[name]
        fields = {"key", "size_bytes", "sha256"}
        if name == "vectors":
            fields |= {"shape", "dtype", "order"}
        require_fields(artifact, fields, "artifacts." + name)
        require_key(artifact["key"])
        if artifact["key"] != f"releases/{release}/{filename}":
            raise ValueError(
                "Artifact key differs from this release's expected location"
            )
        require_positive_int(artifact["size_bytes"], name + ".size_bytes")
        if artifact["size_bytes"] > MAX_FILE_BYTES:
            raise ValueError("Artifact exceeds file size limit")
        require_digest(artifact["sha256"], name + ".sha256")
    vectors = artifacts["vectors"]
    if (
        not isinstance(vectors["shape"], list)
        or len(vectors["shape"]) != 2
        or any(type(v) is not int for v in vectors["shape"])
        or vectors["shape"] != [selection.row_count, profile.dimensions]
        or vectors["dtype"] != profile.dtype
        or vectors["order"] != "C"
    ):
        raise ValueError(
            "Vector shape/type/order differs from the selection and profile"
        )
    if vectors["size_bytes"] <= selection.row_count * profile.dimensions * 4:
        raise ValueError(
            "NPY size must include its declared float32 payload and a header"
        )

    provenance = manifest["provenance"]
    require_fields(
        provenance,
        {"producer_run_id", "zenml_run_id", "mlflow_run_id", "completed_at", "status"},
        "provenance",
    )
    for field in ("producer_run_id", "zenml_run_id"):
        require_uuid(provenance[field], field)
    if not isinstance(provenance["mlflow_run_id"], str) or not re.fullmatch(
        r"[0-9a-f]{32}", provenance["mlflow_run_id"]
    ):
        raise ValueError("mlflow_run_id must be a lowercase 32-character run ID")
    if provenance["status"] != "completed":
        raise ValueError("Only completed producer output can describe a release")
    try:
        completed = datetime.fromisoformat(provenance["completed_at"])
    except (ValueError, TypeError) as exc:
        raise ValueError("completed_at must be an ISO timestamp") from exc
    if completed.utcoffset() is None:
        raise ValueError("completed_at must include a timezone")


def require_promotable(manifest):
    """Publication must call this before updating a compatible full-release pointer."""
    validate_manifest(manifest)
    if manifest["selection"]["kind"] != "full":
        raise ValueError(
            "Subset/test selections cannot promote the full-release pointer"
        )
