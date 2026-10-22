"""Model-free contracts for discovery inputs and whole-review analysis results.

These definitions are shared by producers and consumers. Validation checks
internal consistency, not topic usefulness or the accuracy of model predictions.
"""

import math
from dataclasses import asdict, dataclass
from datetime import datetime

from voc.embeddings.contracts import (
    DatasetIdentity,
    canonical_json,
    fingerprint,
    require_digest,
    require_fields,
    require_key,
    require_positive_int,
    require_uuid,
)

INPUT_SCHEMA = "voc-analysis-input-v1"
SCHEMA_VERSION = "voc-analysis-v1"
# v2 adds the interpretations table. Older candidates stay readable.
SCHEMA_VERSIONS = (SCHEMA_VERSION, "voc-analysis-v2")
SENTIMENT_LABELS = ("negative", "neutral", "positive")
SENTIMENT_STATUSES = ("classified", "excluded", "failed")


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")


def _topic_id(value):
    if type(value) is not int or value < -1:
        raise ValueError("topic_id must be an integer >= -1")


def _number(value, name, *, upper=None):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    if upper is not None and value > upper:
        raise ValueError(f"{name} must not exceed {upper}")


def _ids(values, name):
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"{name} must be a nonempty ordered sequence")
    for value in values:
        _text(value, name)
    if len(set(values)) != len(values):
        raise ValueError(f"{name} contains duplicate IDs")


def validate_input_identity(info):
    """Validate a saved input descriptor without loading vectors or model libraries."""
    require_fields(
        info,
        {
            "schema_version",
            "input_id",
            "dataset",
            "embedding",
            "selection",
            "reviews_sha256",
            "vectors_sha256",
            "vector_shape",
            "vector_dtype",
        },
        "analysis input",
    )
    if info["schema_version"] != INPUT_SCHEMA:
        raise ValueError("Unsupported analysis input schema")
    dataset = DatasetIdentity(**info["dataset"])
    embedding = info["embedding"]
    require_fields(
        embedding,
        {
            "release_id",
            "manifest_sha256",
            "profile_id",
            "profile_name",
            "feature_id",
            "encoder_id",
            "selection_kind",
            "row_count",
        },
        "embedding identity",
    )
    require_uuid(embedding["release_id"], "embedding.release_id")
    for name in ("manifest_sha256", "profile_id", "feature_id", "encoder_id"):
        require_digest(embedding[name], "embedding." + name)
    _text(embedding["profile_name"], "profile_name")
    require_positive_int(embedding["row_count"], "embedding.row_count")
    if embedding["selection_kind"] not in ("full", "first-n"):
        raise ValueError("Invalid embedding selection kind")
    if embedding["row_count"] > dataset.row_count or (
        embedding["selection_kind"] == "full"
        and embedding["row_count"] != dataset.row_count
    ):
        raise ValueError("Embedding coverage differs from dataset")
    selection = info["selection"]
    require_fields(
        selection,
        {
            "kind",
            "size",
            "seed",
            "allocation_policy",
            "record_ids",
            "positions",
            "ordered_ids_sha256",
            "strata",
        },
        "analysis selection",
    )
    kind, size, seed = selection["kind"], selection["size"], selection["seed"]
    if kind not in ("full", "first-n", "stratified"):
        raise ValueError("Unknown analysis selection kind")
    require_positive_int(size, "selection.size")
    if size > embedding["row_count"] or (
        kind == "full" and size != embedding["row_count"]
    ):
        raise ValueError("Selection coverage differs from available embeddings")
    if kind == "stratified":
        if type(seed) is not int or not 0 <= seed < 2**32:
            raise ValueError("Stratified selection requires an unsigned 32-bit seed")
        if (
            selection["allocation_policy"]
            != "platform-month-proportional-largest-remainder-v1"
        ):
            raise ValueError("Unknown stratified allocation policy")
    elif seed is not None or selection["allocation_policy"] != "bundle-order-v1":
        raise ValueError("Full/first-n selection must use bundle order and no seed")
    ids, positions = selection["record_ids"], selection["positions"]
    _ids(ids, "selected record_ids")
    if len(ids) != size or selection["ordered_ids_sha256"] != fingerprint(ids):
        raise ValueError("Ordered selection identity mismatch")
    if (
        not isinstance(positions, list)
        or len(positions) != size
        or any(
            type(v) is not int or not 0 <= v < embedding["row_count"] for v in positions
        )
        or positions != sorted(set(positions))
    ):
        raise ValueError("Selection positions must be unique and preserve bundle order")
    if kind in ("full", "first-n") and positions != list(range(size)):
        raise ValueError("Full/first-n must select the declared prefix")
    strata = selection["strata"]
    if not isinstance(strata, list) or not strata:
        raise ValueError("Selection requires realized platform/month counts")
    keys, available, selected = set(), 0, 0
    for row in strata:
        require_fields(row, {"platform", "month", "available", "selected"}, "stratum")
        for name in ("platform", "month"):
            if row[name] is not None:
                _text(row[name], "stratum." + name)
        key = (row["platform"], row["month"])
        if key in keys:
            raise ValueError("Duplicate stratum")
        keys.add(key)
        require_positive_int(row["available"], "stratum.available")
        if (
            type(row["selected"]) is not int
            or not 0 <= row["selected"] <= row["available"]
        ):
            raise ValueError("Invalid selected stratum count")
        available += row["available"]
        selected += row["selected"]
    if (available, selected) != (embedding["row_count"], size):
        raise ValueError("Stratum counts do not reconcile")
    shape = info["vector_shape"]
    if (
        not isinstance(shape, list)
        or len(shape) != 2
        or shape[0] != size
        or any(type(v) is not int or v <= 0 for v in shape)
        or info["vector_dtype"] != "<f4"
    ):
        raise ValueError("Invalid analysis vector shape/dtype")
    for name in ("reviews_sha256", "vectors_sha256", "input_id"):
        require_digest(info[name], name)
    if info["input_id"] != fingerprint(
        {k: v for k, v in info.items() if k != "input_id"}
    ):
        raise ValueError("Analysis input identity mismatch")


@dataclass(frozen=True)
class ReviewAssignment:
    record_id: str
    topic_id: int
    membership_strength: float

    def __post_init__(self):
        _text(self.record_id, "record_id")
        _topic_id(self.topic_id)
        # HDBSCAN membership strength is not calibrated confidence.
        _number(self.membership_strength, "membership_strength", upper=1)


@dataclass(frozen=True)
class Keyword:
    term: str
    weight: float

    def __post_init__(self):
        _text(self.term, "keyword.term")
        _number(self.weight, "keyword.weight")


@dataclass(frozen=True)
class TopicRecord:
    topic_id: int
    keywords: tuple[Keyword, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    name: str = ""
    description: str = ""

    def __post_init__(self):
        _topic_id(self.topic_id)
        if not isinstance(self.keywords, tuple) or not all(
            isinstance(k, Keyword) for k in self.keywords
        ):
            raise ValueError("keywords must be a tuple of Keyword records")
        if len({k.term for k in self.keywords}) != len(self.keywords):
            raise ValueError("Duplicate topic keywords")
        if not isinstance(self.evidence_ids, tuple):
            raise TypeError("evidence_ids must be a tuple")
        if self.evidence_ids:
            _ids(self.evidence_ids, "evidence_ids")
        if not isinstance(self.name, str) or not isinstance(self.description, str):
            raise TypeError("Topic wording must be strings")


@dataclass(frozen=True)
class ReviewSentiment:
    record_id: str
    status: str
    model_label: str | None = None
    probabilities: dict[str, float] | None = None
    reason: str | None = None
    input_sha256: str | None = None

    def __post_init__(self):
        _text(self.record_id, "record_id")
        if self.status not in SENTIMENT_STATUSES:
            raise ValueError("Unknown terminal sentiment status")
        if self.input_sha256 is not None:
            require_digest(self.input_sha256, "sentiment.input_sha256")
        if self.status != "classified":
            _text(self.reason, "sentiment.reason")
            if self.model_label is not None or self.probabilities is not None:
                raise ValueError(
                    "Unclassified sentiment must not fabricate a label or probabilities"
                )
            return
        require_digest(self.input_sha256, "sentiment.input_sha256")
        if self.reason is not None or self.model_label not in SENTIMENT_LABELS:
            raise ValueError(
                "Classified sentiment requires a valid model label and no failure reason"
            )
        require_fields(self.probabilities, SENTIMENT_LABELS, "sentiment probabilities")
        for value in self.probabilities.values():
            _number(value, "sentiment probability", upper=1)
        if not math.isclose(
            sum(self.probabilities.values()), 1, abs_tol=1e-6, rel_tol=0
        ):
            raise ValueError("Sentiment probabilities must sum to one")
        if (
            self.probabilities[self.model_label]
            < max(self.probabilities.values()) - 1e-8
        ):
            raise ValueError("Model label must have maximal probability")


def validate_results(record_ids, assignments, topics, sentiments):
    """Check complete coverage and evidence membership, returning explicit counts.

    Corrections belong in separate records in future child candidates. They must
    never replace these model labels/probabilities or the original assignments.
    """
    _ids(record_ids, "selected record_ids")
    wanted = set(record_ids)
    for records, cls in (
        (assignments, ReviewAssignment),
        (topics, TopicRecord),
        (sentiments, ReviewSentiment),
    ):
        if not isinstance(records, (list, tuple)) or not all(
            isinstance(r, cls) for r in records
        ):
            raise ValueError(f"Expected a sequence of {cls.__name__}")
        for row in records:
            row.__post_init__()  # Recheck mutable nested probability dictionaries.
    for name, records in (("assignments", assignments), ("sentiments", sentiments)):
        ids = [r.record_id for r in records]
        if len(ids) != len(wanted) or set(ids) != wanted:
            raise ValueError(
                f"{name} must contain exactly one record per selected review"
            )
    topic_ids = [t.topic_id for t in topics]
    if len(set(topic_ids)) != len(topic_ids) or set(topic_ids) != {
        a.topic_id for a in assignments
    }:
        raise ValueError("Topic records must match assignment groups exactly")
    membership = {a.record_id: a.topic_id for a in assignments}
    for topic in topics:
        if any(
            r not in membership or membership[r] != topic.topic_id
            for r in topic.evidence_ids
        ):
            raise ValueError("Topic evidence must reference members of that topic")
    unassigned = sum(a.topic_id == -1 for a in assignments)
    return {
        "selected_reviews": len(record_ids),
        "assigned_reviews": len(record_ids) - unassigned,
        "unassigned_reviews": unassigned,
        "topic_count": sum(t.topic_id >= 0 for t in topics),
        **{
            status: sum(s.status == status for s in sentiments)
            for status in SENTIMENT_STATUSES
        },
    }


@dataclass(frozen=True)
class AnalysisManifest:
    """Completed-result metadata. Artifact existence is checked by the future store."""

    analysis_id: str
    inputs: dict
    configuration: dict
    code_sha256: str
    software: dict[str, str]
    provenance: dict
    artifacts: dict
    coverage: dict
    created_at: str
    parent_analysis_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        self.validate()

    def validate(self):
        require_uuid(self.analysis_id, "analysis_id")
        if self.parent_analysis_id is not None:
            require_uuid(self.parent_analysis_id, "parent_analysis_id")
            if self.parent_analysis_id == self.analysis_id:
                raise ValueError("An analysis cannot be its own parent")
        if self.schema_version not in SCHEMA_VERSIONS:
            raise ValueError("Unsupported analysis schema")
        validate_input_identity(self.inputs)
        require_digest(self.code_sha256, "code_sha256")
        for name, value in (
            ("configuration", self.configuration),
            ("software", self.software),
            ("provenance", self.provenance),
            ("artifacts", self.artifacts),
        ):
            if not isinstance(value, dict) or not value:
                raise ValueError(f"{name} must be a nonempty mapping")
        for key, value in self.software.items():
            _text(key, "software name")
            _text(value, "software version")
        require_uuid(self.provenance.get("run_id"), "provenance.run_id")
        for name, artifact in self.artifacts.items():
            _text(name, "artifact name")
            require_fields(artifact, {"path", "sha256", "size_bytes"}, "artifact")
            require_key(artifact["path"])
            require_digest(artifact["sha256"], "artifact.sha256")
            require_positive_int(artifact["size_bytes"], "artifact.size_bytes")
        if len({a["path"] for a in self.artifacts.values()}) != len(self.artifacts):
            raise ValueError("Artifacts must have distinct paths")
        require_fields(
            self.coverage,
            {
                "selected_reviews",
                "assigned_reviews",
                "unassigned_reviews",
                "topic_count",
                *SENTIMENT_STATUSES,
            },
            "analysis coverage",
        )
        c = self.coverage
        if any(type(v) is not int or v < 0 for v in c.values()):
            raise ValueError("Coverage must contain nonnegative integer counts")
        if (
            c["selected_reviews"] != self.inputs["selection"]["size"]
            or c["assigned_reviews"] + c["unassigned_reviews"] != c["selected_reviews"]
            or sum(c[k] for k in SENTIMENT_STATUSES) != c["selected_reviews"]
            or c["topic_count"] > c["assigned_reviews"]
            or bool(c["topic_count"]) != bool(c["assigned_reviews"])
        ):
            raise ValueError("Analysis coverage does not reconcile")
        try:
            timestamp = datetime.fromisoformat(self.created_at)
        except (TypeError, ValueError) as error:
            raise ValueError("created_at must be an ISO timestamp") from error
        if timestamp.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        canonical_json(asdict(self))  # Refuse nonportable values and NaN in metadata.

    def as_dict(self):
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        # Candidates written by the prototype carry an empty corrections list;
        # human corrections themselves are not part of this release.
        if value.get("corrections", []) in ([], ()):
            value.pop("corrections", None)
        else:
            raise ValueError("Correction records are not supported")
        return cls(**value)
