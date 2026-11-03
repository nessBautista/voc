"""Portable selection identities for larger samples and complete populations."""

import hashlib
from dataclasses import asdict, dataclass

import numpy as np

from voc.embeddings.shared.contracts import canonical_json, require_digest

SCHEMA_V2 = "voc-local-topics-v2"
INPUT_SCHEMA = "voc-topic-input-v2"
MAX_ROWS = 100_000
VECTOR_HASH = "float32-le-c-order-sha256-v1"
SAMPLE_POLICY = "platform-month-proportional-largest-remainder-v1"
FULL_POLICY = "full-record-id-order-v1"


@dataclass(frozen=True)
class TopicSelection:
    mode: str = "sample"
    sample_size: int | None = 5000
    sample_seed: int | None = 42

    def __post_init__(self):
        if self.mode == "full":
            if self.sample_size is not None or self.sample_seed is not None:
                raise ValueError("Full selection has no sample size or seed")
        elif self.mode == "sample":
            if (
                type(self.sample_size) is not int
                or not 20 <= self.sample_size <= MAX_ROWS
            ):
                raise ValueError("Sample size must be in [20, 100000]")
            if (
                type(self.sample_seed) is not int
                or not 0 <= self.sample_seed <= 2**32 - 1
            ):
                raise ValueError("Invalid sample seed")
        else:
            raise ValueError("Unknown topic selection mode")

    def as_dict(self):
        return asdict(self)


def vector_hash(vectors):
    """Hash bounded canonical byte blocks, without a whole-matrix Python list."""
    if (
        vectors.ndim != 2
        or not 1 <= vectors.shape[0] <= MAX_ROWS
        or vectors.shape[1] != 384
        or vectors.dtype.kind != "f"
        or vectors.dtype.itemsize != 4
    ):
        raise ValueError("Invalid topic vector matrix")
    header = canonical_json(
        {"shape": list(vectors.shape), "dtype": "<f4", "order": "C"}
    )
    digest = hashlib.sha256(VECTOR_HASH.encode() + b"\0" + header + b"\0")
    for start in range(0, len(vectors), 4096):
        block = np.ascontiguousarray(vectors[start : start + 4096], dtype="<f4")
        if not np.isfinite(block).all():
            raise ValueError("Nonfinite topic vectors")
        if not np.allclose(np.linalg.norm(block, axis=1), 1, atol=1e-4):
            raise ValueError("Topic vectors must be normalized")
        digest.update(memoryview(block).cast("B"))
    return digest.hexdigest()


def validate_scope(selection, inputs, population_rows):
    """Reconcile portable scope and completeness, independently of model code."""
    from .contracts import selection_hash

    if type(population_rows) is not int or not 1 <= population_rows <= MAX_ROWS:
        raise ValueError("Population exceeds topic scope")
    if (
        inputs["schema_version"] != INPUT_SCHEMA
        or inputs["vectors_hash_algorithm"] != VECTOR_HASH
    ):
        raise ValueError("Unsupported topic input identity")
    plan = TopicSelection(**inputs["selection"])
    if (
        type(inputs["selected_rows"]) is not int
        or type(inputs["population_rows"]) is not int
        or inputs["selected_rows"] != len(selection)
        or inputs["population_rows"] != population_rows
    ):
        raise ValueError("Selection counts differ from population")
    if (selection.source_row >= population_rows).any():
        raise ValueError("Selection contains an unknown source position")
    require_digest(inputs["population_selection_sha256"], "population_selection_sha256")
    require_digest(inputs["vectors_sha256"], "vectors_sha256")
    if inputs["selection_sha256"] != selection_hash(selection):
        raise ValueError("Selection identity changed")
    expected_policy = FULL_POLICY if plan.mode == "full" else SAMPLE_POLICY
    if inputs["selection_policy"] != expected_policy:
        raise ValueError("Selection policy differs from mode")
    if plan.mode == "full":
        # Unique bounded source positions plus equal count cover every original row.
        if (
            len(selection) != population_rows
            or inputs["selection_sha256"] != inputs["population_selection_sha256"]
        ):
            raise ValueError("Full selection omits or changes population rows")
    elif len(selection) != plan.sample_size or len(selection) > population_rows:
        raise ValueError("Sample size differs from selection")
    validate_coverage(selection, inputs["coverage"], population_rows)


def validate_coverage(selection, coverage, population_rows):
    """Reconcile every stratum, including those absent from a sampled input."""
    keys = [(row["platform"], row["period"]) for row in coverage]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate population coverage")
    actual = selection.groupby(["platform", "period"]).size().to_dict()
    if not set(actual) <= set(keys):
        raise ValueError("Selection contains unknown stratum")
    for row in coverage:
        if (
            type(row["population"]) is not int
            or type(row["selected"]) is not int
            or not 0 <= row["selected"] <= row["population"]
            or row["selected"] != actual.get((row["platform"], row["period"]), 0)
        ):
            raise ValueError("Sample coverage differs from selection")
    if sum(row["population"] for row in coverage) != population_rows:
        raise ValueError("Invalid population coverage")
