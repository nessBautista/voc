"""Small, closed configuration schemas. No models or services are initialized."""

import math
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from voc.embeddings.contracts import fingerprint


def _integer(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class SelectionConfig:
    """Full means all rows in the loaded embedding bundle, including explicit subsets."""

    kind: str = "full"
    size: int | None = None
    seed: int | None = None

    def __post_init__(self):
        if self.kind not in ("full", "first-n", "stratified"):
            raise ValueError("Selection kind must be full, first-n or stratified")
        if self.kind == "full":
            if self.size is not None:
                raise ValueError("Full selection cannot specify size")
        else:
            _integer(self.size, "selection.size")
        if self.kind == "stratified":
            _integer(self.seed, "selection.seed", 0)
            if self.seed >= 2**32:
                raise ValueError("selection.seed must be below 2**32")
        elif self.seed is not None:
            raise ValueError("Only stratified selection accepts a seed")


@dataclass(frozen=True)
class ReductionConfig:
    n_neighbors: int = 15
    n_components: int = 5
    min_dist: float = 0.0
    metric: str = "cosine"
    random_state: int = 42

    def __post_init__(self):
        _integer(self.n_neighbors, "n_neighbors", 2)
        _integer(self.n_components, "n_components", 2)
        _integer(self.random_state, "random_state", 0)
        if self.random_state >= 2**32:
            raise ValueError("random_state must be below 2**32")
        if (
            type(self.min_dist) not in (int, float)
            or not math.isfinite(self.min_dist)
            or not 0 <= self.min_dist <= 1
        ):
            raise ValueError("min_dist must be finite and between zero and one")
        if self.metric not in ("cosine", "euclidean"):
            raise ValueError("Unsupported reduction metric")


@dataclass(frozen=True)
class ClusteringConfig:
    min_cluster_size: int = 10
    min_samples: int = 3
    metric: str = "euclidean"
    cluster_selection_method: str = "eom"

    def __post_init__(self):
        _integer(self.min_cluster_size, "min_cluster_size", 2)
        _integer(self.min_samples, "min_samples")
        if self.metric != "euclidean" or self.cluster_selection_method not in (
            "eom",
            "leaf",
        ):
            raise ValueError("Unsupported clustering metric or selection method")


@dataclass(frozen=True)
class AnalysisConfig:
    """BERTopic is the initial backend. Other backends require their own implementation."""

    backend: str = "bertopic"
    selection: SelectionConfig = field(default_factory=SelectionConfig)
    reduction: ReductionConfig = field(default_factory=ReductionConfig)
    clustering: ClusteringConfig = field(default_factory=ClusteringConfig)

    def __post_init__(self):
        if self.backend != "bertopic":
            raise ValueError("Only the initial bertopic backend is configured")
        for value, cls in (
            (self.selection, SelectionConfig),
            (self.reduction, ReductionConfig),
            (self.clustering, ClusteringConfig),
        ):
            if not isinstance(value, cls):
                raise TypeError(f"Expected {cls.__name__}")

    def validate_row_count(self, count):
        _integer(count, "selected rows")
        if count <= max(self.reduction.n_neighbors, self.reduction.n_components + 1):
            raise ValueError("Selection is too small for configured reduction")
        if count < max(self.clustering.min_samples, self.clustering.min_cluster_size):
            raise ValueError("Selection is too small for configured clustering")

    def as_dict(self):
        return {
            "topics": {"backend": self.backend},
            "selection": asdict(self.selection),
            "reduction": asdict(self.reduction),
            "clustering": asdict(self.clustering),
        }

    @property
    def config_id(self):
        return fingerprint(self.as_dict())

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) - {
            "topics",
            "selection",
            "reduction",
            "clustering",
        }:
            raise ValueError("Unknown analysis configuration section")
        topics = value.get("topics", {})
        if not isinstance(topics, dict) or set(topics) - {"backend"}:
            raise ValueError("Unknown topics configuration key")
        parts = {}
        for name, kind in (
            ("selection", SelectionConfig),
            ("reduction", ReductionConfig),
            ("clustering", ClusteringConfig),
        ):
            raw = value.get(name, {})
            if not isinstance(raw, dict) or set(raw) - set(kind.__dataclass_fields__):
                raise ValueError(f"Unknown {name} configuration key")
            parts[name] = kind(**raw)
        return cls(backend=topics.get("backend", "bertopic"), **parts)


def load_config(path):
    """Read TOML, resolve defaults and reject unknown options before any inference."""
    with Path(path).open("rb") as stream:
        return AnalysisConfig.from_dict(tomllib.load(stream))
