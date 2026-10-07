"""One explicit baseline; profile resolution never imports or downloads a model."""

import re
from dataclasses import asdict, dataclass

from .contracts import fingerprint, require_positive_int


@dataclass(frozen=True)
class EmbeddingProfile:
    name: str
    model: str
    revision: str
    dimensions: int = 384
    dtype: str = "<f4"
    normalize: bool = True
    max_length: int = 128
    prompt: str | None = None
    preparation_policy: str = "verbatim-v1"

    def __post_init__(self):
        if not isinstance(self.name, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9-]*", self.name
        ):
            raise ValueError("Profile name must be a versioned lowercase identifier")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Model name is required")
        if not isinstance(self.revision, str) or not re.fullmatch(
            r"[0-9a-f]{40}", self.revision
        ):
            raise ValueError("Model revision must be a pinned 40-character commit")
        require_positive_int(self.dimensions, "dimensions")
        require_positive_int(self.max_length, "max_length")
        if self.dtype != "<f4" or type(self.normalize) is not bool:
            raise ValueError(
                "Declare little-endian float32 and an explicit normalization boolean"
            )
        if self.prompt is not None and not isinstance(self.prompt, str):
            raise ValueError("prompt must be a string or None")
        if (
            not isinstance(self.preparation_policy, str)
            or not self.preparation_policy.strip()
        ):
            raise ValueError("preparation_policy is required")

    def config(self):
        values = asdict(self)
        del values["name"]
        return values

    @property
    def profile_id(self):
        return fingerprint(
            {"identity_schema": "voc-profile-v1", "config": self.config()}
        )

    def as_dict(self):
        return {
            "name": self.name,
            "profile_id": self.profile_id,
            "config": self.config(),
        }


MINILM_VERBATIM_V1 = EmbeddingProfile(
    name="minilm-verbatim-v1",
    model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    revision="e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
)


def resolve_profile(name="minilm-verbatim-v1"):
    if name != MINILM_VERBATIM_V1.name:
        raise ValueError(f"Unknown embedding profile: {name!r}")
    return MINILM_VERBATIM_V1
