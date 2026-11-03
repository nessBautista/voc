"""Read immutable local topic results with pinned integrity and semantic checks."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from voc.datasets.shared.manifest import file_hash
from voc.embeddings.shared.contracts import (
    dataset_identity,
    require_digest,
    require_uuid,
)

from .contracts import SCHEMA, selection_hash, validate_results
from .representation import REPRESENTATION_SCHEMA, validate_representation

FILES = {
    "selection.parquet",
    "assignments.parquet",
    "topics.json",
    "coordinates.npy",
    "execution.json",
}


@dataclass
class TopicBundle:
    reviews: pd.DataFrame
    assignments: pd.DataFrame
    topics: list
    coordinates: np.ndarray
    execution: dict
    info: dict

    def topic(self, topic_id):
        """Return the saved topic with its referenced texts, without model calls."""
        for topic in self.topics:
            if topic["topic_id"] == topic_id:
                indexed = self.reviews.set_index("record_id", drop=False)
                evidence = indexed.loc[topic["evidence_ids"]]
                return {
                    **topic,
                    "evidence": json.loads(evidence.to_json(orient="records")),
                }
        raise LookupError(f"Unknown topic: {topic_id}")


def read_topics(directory, *, manifest_sha256):
    directory = Path(directory)
    require_digest(manifest_sha256, "manifest_sha256")
    path = directory / "manifest.json"
    if (
        path.is_symlink()
        or not 0 < path.stat().st_size <= 2**20
        or file_hash(path) != manifest_sha256
    ):
        raise ValueError("Topic manifest checksum or size mismatch")
    manifest = json.loads(path.read_bytes())
    if (
        manifest["schema_version"] not in (SCHEMA, REPRESENTATION_SCHEMA)
        or manifest["state"] != "completed"
    ):
        raise ValueError("Unsupported or incomplete topic artifact")
    require_uuid(manifest["run_id"], "run_id")
    if set(manifest["files"]) != FILES:
        raise ValueError("Unexpected topic artifact files")
    for name, item in manifest["files"].items():
        path = directory / name
        if (
            path.is_symlink()
            or not 0 < path.stat().st_size <= 64 * 2**20
            or path.stat().st_size != item["size_bytes"]
            or file_hash(path) != item["sha256"]
        ):
            raise ValueError(f"Topic artifact checksum or size mismatch: {name}")
    selection = pd.read_parquet(directory / "selection.parquet")
    assignments = pd.read_parquet(directory / "assignments.parquet")
    topics = json.loads((directory / "topics.json").read_bytes())
    execution = json.loads((directory / "execution.json").read_bytes())
    coordinates = np.load(directory / "coordinates.npy", allow_pickle=False)
    counts = validate_results(selection, assignments, topics, coordinates)
    inputs = manifest["input"]
    from voc.embeddings.shared.profiles import resolve_profile

    if inputs["profile"] != resolve_profile().as_dict():
        raise ValueError("Unsupported topic input embedding profile")
    if inputs["selection_policy"] != "platform-month-proportional-largest-remainder-v1":
        raise ValueError("Unsupported topic selection policy")
    identity = dataset_identity(inputs["dataset"])
    require_uuid(inputs["embedding_release_id"], "embedding_release_id")
    require_digest(inputs["embedding_manifest_sha256"], "embedding_manifest_sha256")
    require_digest(inputs["vectors_sha256"], "vectors_sha256")
    if (
        (selection.source_row >= identity.row_count).any()
        or inputs["sample_size"] != len(selection)
        or inputs["selection_sha256"] != selection_hash(selection)
    ):
        raise ValueError("Selection differs from manifest input")
    if counts != manifest["counts"] or execution["run_id"] != manifest["run_id"]:
        raise ValueError("Execution/counts differ from manifest")
    if (
        execution["input"] != inputs
        or execution["configuration"] != manifest["configuration"]
    ):
        raise ValueError("Execution lineage differs from manifest")
    config = manifest["configuration"]
    if (
        config["sample_size"] != len(selection)
        or config["sample_seed"] != inputs["sample_seed"]
    ):
        raise ValueError("Configuration differs from selection")
    if coordinates.shape[1] != config["n_components"]:
        raise ValueError("Coordinates differ from reduction dimensions")
    # Reconcile per-stratum counts, including strata absent from the sample.
    coverage = inputs["coverage"]
    keys = [(row["platform"], row["period"]) for row in coverage]
    if (
        len(keys) != len(set(keys))
        or sum(row["population"] for row in coverage) != identity.row_count
    ):
        raise ValueError("Invalid population coverage")
    actual = selection.groupby(["platform", "period"]).size().to_dict()
    if not set(actual) <= set(keys):
        raise ValueError("Sample contains unknown stratum")
    for row in coverage:
        if (
            type(row["population"]) is not int
            or type(row["selected"]) is not int
            or not 0 <= row["selected"] <= row["population"]
            or row["selected"] != actual.get((row["platform"], row["period"]), 0)
        ):
            raise ValueError("Sample coverage differs from selection")
    if manifest["schema_version"] == REPRESENTATION_SCHEMA:
        validate_representation(manifest, execution, topics)
    return TopicBundle(selection, assignments, topics, coordinates, execution, manifest)
