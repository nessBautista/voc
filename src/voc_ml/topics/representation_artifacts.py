"""Self-contained immutable representation exports with preserved parent files."""

import os
import shutil
import tempfile
from pathlib import Path

from voc.datasets.shared.manifest import file_hash
from voc.embeddings.shared.contracts import canonical_json, fingerprint
from voc.embeddings.shared.filesystem import sync_directory
from voc.topics import read_topics
from voc.topics.reading import FILES
from voc.topics.representation import (
    REPRESENTATION_SCHEMA,
    REPRESENTATION_SCHEMA_V2,
    topic_skeleton,
)


def export_representation(parent_directory, parent_hash, topics, execution, directory):
    parent_directory, destination = Path(parent_directory), Path(directory)
    parent = read_topics(parent_directory, manifest_sha256=parent_hash)
    if parent.info["schema_version"] not in (
        "voc-local-topics-v1",
        "voc-local-topics-v2",
    ):
        raise ValueError("Expected grouping parent")
    if topic_skeleton(topics) != topic_skeleton(parent.topics):
        raise ValueError("Refinement changed parent topic membership or evidence")
    if (
        execution["configuration"] != parent.info["configuration"]
        or execution["input"] != parent.info["input"]
    ):
        raise ValueError("Refinement changed parent lineage")
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    execution = {
        **execution,
        "parent_manifest": parent.info,
        "parent_manifest_sha256": parent_hash,
        "parent_topic_skeleton_sha256": fingerprint(topic_skeleton(parent.topics)),
    }
    with tempfile.TemporaryDirectory(
        prefix=".representation-", dir=destination.parent
    ) as temp:
        staging = Path(temp)
        for name in ("selection.parquet", "assignments.parquet", "coordinates.npy"):
            shutil.copyfile(parent_directory / name, staging / name)
        (staging / "topics.json").write_bytes(canonical_json(topics))
        (staging / "execution.json").write_bytes(canonical_json(execution))
        manifest = {
            **parent.info,
            "schema_version": REPRESENTATION_SCHEMA_V2
            if parent.info["schema_version"] == "voc-local-topics-v2"
            else REPRESENTATION_SCHEMA,
            "run_id": execution["run_id"],
            "files": {
                name: {
                    "sha256": file_hash(staging / name),
                    "size_bytes": (staging / name).stat().st_size,
                }
                for name in sorted(FILES)
            },
        }
        (staging / "manifest.json").write_bytes(canonical_json(manifest))
        digest = file_hash(staging / "manifest.json")
        read_topics(staging, manifest_sha256=digest)
        # Also detect a changed source while the child was being constructed.
        read_topics(parent_directory, manifest_sha256=parent_hash)
        for path in staging.iterdir():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        if destination.exists():
            raise FileExistsError(destination)
        sync_directory(staging)
        staging.rename(destination)
        sync_directory(destination.parent)
    return {
        "directory": str(destination),
        "manifest_sha256": digest,
        "run_id": execution["run_id"],
    }
