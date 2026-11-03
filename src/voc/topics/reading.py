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
from .representation import (
    REPRESENTATION_SCHEMA,
    REPRESENTATION_SCHEMA_V2,
    validate_representation,
)
from .scope import SCHEMA_V2, validate_coverage, validate_scope

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
        manifest["schema_version"]
        not in (SCHEMA, REPRESENTATION_SCHEMA, SCHEMA_V2, REPRESENTATION_SCHEMA_V2)
        or manifest["state"] != "completed"
    ):
        raise ValueError("Unsupported or incomplete topic artifact")
    require_uuid(manifest["run_id"], "run_id")
    if set(manifest["files"]) != FILES:
        raise ValueError("Unexpected topic artifact files")
    is_v2 = manifest["schema_version"] in (SCHEMA_V2, REPRESENTATION_SCHEMA_V2)
    from .files import FILE_LIMITS, read_tables

    for name, item in manifest["files"].items():
        path = directory / name
        if (
            path.is_symlink()
            or not 0
            < path.stat().st_size
            <= (FILE_LIMITS[name] if is_v2 else 64 * 2**20)
            or path.stat().st_size != item["size_bytes"]
            or file_hash(path) != item["sha256"]
        ):
            raise ValueError(f"Topic artifact checksum or size mismatch: {name}")
    if is_v2:
        selection, assignments, coordinates = read_tables(directory)
    else:
        selection = pd.read_parquet(directory / "selection.parquet")
        assignments = pd.read_parquet(directory / "assignments.parquet")
        coordinates = np.load(directory / "coordinates.npy", allow_pickle=False)
    topics = json.loads((directory / "topics.json").read_bytes())
    execution = json.loads((directory / "execution.json").read_bytes())
    counts = validate_results(
        selection,
        assignments,
        topics,
        coordinates,
        schema=SCHEMA_V2 if is_v2 else SCHEMA,
    )
    inputs = manifest["input"]
    from voc.embeddings.shared.profiles import resolve_profile

    if inputs["profile"] != resolve_profile().as_dict():
        raise ValueError("Unsupported topic input embedding profile")
    if (
        not is_v2
        and inputs["selection_policy"]
        != "platform-month-proportional-largest-remainder-v1"
    ):
        raise ValueError("Unsupported topic selection policy")
    identity = dataset_identity(inputs["dataset"])
    require_uuid(inputs["embedding_release_id"], "embedding_release_id")
    require_digest(inputs["embedding_manifest_sha256"], "embedding_manifest_sha256")
    require_digest(inputs["vectors_sha256"], "vectors_sha256")
    if is_v2:
        validate_scope(selection, inputs, identity.row_count)
    elif (
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
    if is_v2:
        if config["selection"] != inputs["selection"]:
            raise ValueError("Configuration differs from selection")
        dimensions = config["model"]["n_components"]
        if type(dimensions) is not int or not 2 <= dimensions <= 20:
            raise ValueError("Invalid model dimensions")
    elif (
        config["sample_size"] != len(selection)
        or config["sample_seed"] != inputs["sample_seed"]
    ):
        raise ValueError("Configuration differs from selection")
    if coordinates.shape[1] != (dimensions if is_v2 else config["n_components"]):
        raise ValueError("Coordinates differ from reduction dimensions")
    if not is_v2:
        validate_coverage(selection, inputs["coverage"], identity.row_count)
    if manifest["schema_version"] in (REPRESENTATION_SCHEMA, REPRESENTATION_SCHEMA_V2):
        validate_representation(manifest, execution, topics)
    return TopicBundle(selection, assignments, topics, coordinates, execution, manifest)
