"""The supported preparation policy and persisted feature format."""

from dataclasses import dataclass

SCHEMA_VERSION = "voc-features-v1"
FEATURE_COLUMNS = ("embedding_text", "source_text_hash", "feature_text_hash")


@dataclass(frozen=True)
class FeatureConfig:
    policy: str = "verbatim-v1"

    def __post_init__(self):
        if self.policy != "verbatim-v1":
            raise ValueError(f"Unsupported preparation policy: {self.policy!r}")
