"""Bounded discovery configuration, without importing model libraries."""

import math
import tomllib
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TopicConfig:
    sample_size: int = 1000
    sample_seed: int = 42
    n_neighbors: int = 15
    n_components: int = 5
    min_dist: float = 0.0
    random_state: int = 42
    min_cluster_size: int = 10
    min_samples: int = 3
    cluster_selection_method: str = "eom"
    top_n_words: int = 10
    timeout_seconds: int = 600
    threads: int = 4

    def __post_init__(self):
        limits = {
            "sample_size": (20, 1000),
            "sample_seed": (0, 2**32 - 1),
            "n_neighbors": (2, 100),
            "n_components": (2, 20),
            "random_state": (0, 2**32 - 1),
            "min_cluster_size": (2, 1000),
            "min_samples": (1, 1000),
            "top_n_words": (1, 30),
            "timeout_seconds": (1, 600),
            "threads": (1, 4),
        }
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in [{low}, {high}]")
        if (
            type(self.min_dist) not in (int, float)
            or not math.isfinite(self.min_dist)
            or not 0 <= self.min_dist <= 1
        ):
            raise ValueError("min_dist must be finite and in [0, 1]")
        if self.cluster_selection_method not in ("eom", "leaf"):
            raise ValueError("Unknown cluster_selection_method")
        if self.sample_size <= max(self.n_neighbors, self.n_components + 1):
            raise ValueError("Sample is too small for UMAP")
        if self.sample_size < max(self.min_cluster_size, self.min_samples):
            raise ValueError("Sample is too small for HDBSCAN")

    def as_dict(self):
        return asdict(self)


def load_config(path):
    with open(path, "rb") as stream:
        values = tomllib.load(stream)
    try:
        return TopicConfig(**values)
    except TypeError as error:
        raise ValueError("Unknown topic configuration field") from error
