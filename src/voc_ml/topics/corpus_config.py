"""Versioned selection and execution limits, separate from model parameters."""

from dataclasses import dataclass, field

from voc.topics.scope import MAX_ROWS, TopicSelection

from .config import TopicConfig

SCHEMA = "voc-topic-configuration-v2"
MODEL_FIELDS = set(TopicConfig().as_dict()) - {
    "sample_size",
    "sample_seed",
    "threads",
    "timeout_seconds",
}


@dataclass(frozen=True)
class CorpusConfig:
    selection: TopicSelection = field(default_factory=TopicSelection)
    rows: int = 5000
    model: dict = field(
        default_factory=lambda: {
            k: v for k, v in TopicConfig().as_dict().items() if k in MODEL_FIELDS
        }
    )
    threads: int = 4
    timeout_seconds: int = 600

    def __post_init__(self):
        self.selection.__post_init__()
        if type(self.rows) is not int or not 20 <= self.rows <= MAX_ROWS:
            raise ValueError("Invalid corpus configuration row count")
        if self.selection.mode == "sample" and self.rows != self.selection.sample_size:
            raise ValueError("Corpus sample size differs from row count")
        if set(self.model) != MODEL_FIELDS:
            raise ValueError("Invalid corpus model fields")
        # Reuse model validation without relaxing legacy sample/runtime limits.
        TopicConfig(**self.model, threads=self.threads)
        if (
            type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= 3600
        ):
            raise ValueError("Invalid corpus worker timeout")
        if self.rows <= max(self.model["n_neighbors"], self.model["n_components"] + 1):
            raise ValueError("Selection too small for reduction")
        if self.rows < max(self.model["min_samples"], self.model["min_cluster_size"]):
            raise ValueError("Selection too small for clustering")

    @property
    def sample_size(self):
        return self.rows

    def __getattr__(self, name):
        if name in MODEL_FIELDS:
            return self.model[name]
        raise AttributeError(name)

    def as_dict(self):
        return {
            "schema_version": SCHEMA,
            "selection": self.selection.as_dict(),
            "rows": self.rows,
            "model": dict(self.model),
            "execution": {
                "threads": self.threads,
                "worker_seconds": self.timeout_seconds,
            },
        }


def resolve_config(values):
    if "schema_version" not in values:
        return TopicConfig(**values)
    if values.get("schema_version") != SCHEMA or set(values) != {
        "schema_version",
        "selection",
        "rows",
        "model",
        "execution",
    }:
        raise ValueError("Unsupported topic configuration")
    if set(values["execution"]) != {"threads", "worker_seconds"}:
        raise ValueError("Invalid execution configuration")
    return CorpusConfig(
        selection=TopicSelection(**values["selection"]),
        rows=values["rows"],
        model=values["model"],
        threads=values["execution"]["threads"],
        timeout_seconds=values["execution"]["worker_seconds"],
    )


def tracking_parameters(values):
    config = resolve_config(values)
    if isinstance(config, TopicConfig):
        return values
    return {
        **{"model_" + k: v for k, v in config.model.items()},
        **{"selection_" + k: v for k, v in config.selection.as_dict().items()},
        "rows": config.rows,
        "threads": config.threads,
        "worker_seconds": config.timeout_seconds,
    }
