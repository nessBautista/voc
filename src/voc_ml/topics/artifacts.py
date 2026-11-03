"""Atomic local topic exports validated through the public consumer."""

import os
import tempfile
from pathlib import Path

import numpy as np

from voc.datasets.shared.manifest import file_hash
from voc.embeddings.shared.contracts import canonical_json
from voc.embeddings.shared.filesystem import sync_directory
from voc.topics import read_topics
from voc.topics.contracts import SCHEMA, validate_results
from voc.topics.reading import FILES


def export_topics(inputs, assignments, topics, coordinates, execution, directory):
    inputs.validate()
    counts = validate_results(inputs.reviews, assignments, topics, coordinates)
    destination = Path(directory)
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".topics-", dir=destination.parent) as temp:
        staging = Path(temp)
        inputs.reviews.to_parquet(staging / "selection.parquet", index=False)
        assignments.to_parquet(staging / "assignments.parquet", index=False)
        np.save(staging / "coordinates.npy", coordinates, allow_pickle=False)
        (staging / "topics.json").write_bytes(canonical_json(topics))
        (staging / "execution.json").write_bytes(canonical_json(execution))
        manifest = {
            "schema_version": SCHEMA,
            "state": "completed",
            "run_id": execution["run_id"],
            "input": inputs.info,
            "configuration": execution["configuration"],
            "counts": counts,
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
