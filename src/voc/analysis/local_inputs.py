"""Read independent local copies of published inputs without cloud or ML clients."""

import json
import tomllib
from pathlib import Path

import pandas as pd

from voc.datasets.manifest import file_hash, frame_hash
from voc.embeddings.contracts import DatasetIdentity, require_digest
from voc.embeddings.profiles import resolve_profile
from voc.embeddings.validation import load_release
from voc.models import Snapshot


def load_local_config(path):
    """Freeze paths relative to the TOML file, with explicit manifest checksums."""
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        value = tomllib.load(stream)
    paths = ("dataset", "dataset_manifest", "embeddings")
    hashes = ("dataset_manifest_sha256", "embedding_manifest_sha256")
    if set(value) != set(paths + hashes):
        raise ValueError(
            "Local inputs require dataset, dataset_manifest, embeddings and both manifest hashes"
        )
    for key in hashes:
        require_digest(value[key], key)
    for key in paths:
        value[key] = str((path.parent / value[key]).resolve(strict=True))
    return value


def read_local_inputs(value, *, profile="minilm-verbatim-v1"):
    """Verify historical provenance, complete corpus content and vector alignment.

    The original S3 identity is preserved as provenance; no S3 request is made.
    Local paths describe transport, not a new publication or a new dataset.
    """
    manifest_path = Path(value["dataset_manifest"])
    if file_hash(manifest_path) != value["dataset_manifest_sha256"]:
        raise ValueError("Local dataset manifest checksum mismatch")
    manifest = json.loads(manifest_path.read_bytes())
    metadata = manifest["workable"]
    dataset_path = Path(value["dataset"])
    if file_hash(dataset_path) != metadata["sha256"]:
        raise ValueError("Local dataset file checksum mismatch")
    frame = pd.read_parquet(dataset_path)
    if (
        len(frame) != metadata["row_count"]
        or frame_hash(frame) != metadata["content_sha256"]
    ):
        raise ValueError("Local dataset content differs from its manifest")
    expected = frame[["record_id", "model_text"]].reset_index(drop=True)
    expected["source_row"] = range(len(expected))
    bundle = load_release(
        value["embeddings"],
        manifest_sha256=value["embedding_manifest_sha256"],
        expected_reviews=expected,
    )
    identity = DatasetIdentity(**bundle.info["dataset"])
    if (
        identity.release_id != manifest["release_id"]
        or identity.content_sha256 != metadata["content_sha256"]
        or identity.file_sha256 != metadata["sha256"]
        or identity.row_count != len(frame)
        or bundle.info["profile"] != resolve_profile(profile).as_dict()
    ):
        raise ValueError("Local corpus and embedding provenance differ")
    return Snapshot(frame, identity.as_dict()), bundle
