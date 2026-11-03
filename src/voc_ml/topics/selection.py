"""Deterministic proportional source/month sampling of verified local inputs."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from voc.embeddings import get_local_embeddings
from voc.embeddings.shared.contracts import fingerprint
from voc.topics.contracts import selection_hash, validate_selection

from .config import TopicConfig


@dataclass
class TopicInput:
    reviews: pd.DataFrame
    vectors: np.ndarray
    info: dict

    def validate(self):
        if self.info.get("schema_version") == "voc-topic-input-v2":
            from voc.topics.scope import SCHEMA_V2, validate_scope, vector_hash

            validate_selection(self.reviews, schema=SCHEMA_V2)
            validate_scope(self.reviews, self.info, self.info["dataset"]["row_count"])
            if vector_hash(self.vectors) != self.info["vectors_sha256"]:
                raise ValueError("Topic input vectors changed")
            return
        if "schema_version" in self.info:
            raise ValueError("Unsupported topic input schema")
        validate_selection(self.reviews)
        if selection_hash(self.reviews) != self.info["selection_sha256"]:
            raise ValueError("Topic input text/order/metadata changed")
        if (
            self.vectors.shape != (len(self.reviews), 384)
            or self.vectors.dtype != np.dtype("float32")
            or not np.isfinite(self.vectors).all()
        ):
            raise ValueError("Invalid topic input vectors")
        if fingerprint(self.vectors.tolist()) != self.info["vectors_sha256"]:
            raise ValueError("Topic input vectors changed")
        if not np.allclose(np.linalg.norm(self.vectors, axis=1), 1, atol=1e-4):
            raise ValueError("Topic input vectors must be normalized")


def sample_rows(frame, config):
    """Largest remainder quotas, stable ties, one seeded RNG, no replacement."""
    config.__post_init__()
    if len(frame) < config.sample_size:
        raise ValueError("Population is smaller than the requested sample")
    if not frame.record_id.is_unique:
        raise ValueError("Duplicate population record IDs")
    if not frame.platform.isin(["app_store", "google_play", "x", "youtube"]).all():
        raise ValueError("Unknown population platform")
    work = frame.sort_values("record_id").copy()
    dates = pd.to_datetime(work.record_date, format="mixed", errors="coerce", utc=True)
    work["period"] = dates.dt.strftime("%Y-%m").fillna("unknown")
    groups = list(work.groupby(["platform", "period"], sort=True))
    counts = np.array([len(part) for _, part in groups], dtype=np.int64)
    # Integer arithmetic makes rounding and quota ties reproducible.
    quotas, remainders = np.divmod(counts * config.sample_size, len(work))
    order = sorted(
        range(len(groups)), key=lambda i: (-int(remainders[i]), groups[i][0])
    )
    for index in order[: config.sample_size - int(quotas.sum())]:
        quotas[index] += 1
    rng = np.random.default_rng(config.sample_seed)
    parts, coverage = [], []
    for ((platform, period), part), quota in zip(groups, quotas, strict=True):
        chosen = rng.choice(len(part), size=int(quota), replace=False)
        parts.append(part.iloc[chosen])
        coverage.append(
            {
                "platform": platform,
                "period": period,
                "population": len(part),
                "selected": int(quota),
            }
        )
    selected = pd.concat(parts).sort_values("record_id").reset_index(drop=True)
    return selected, coverage


def select_input(
    snapshot, directory, manifest_sha256, config=None, *, read_embeddings=None
):
    """Read and align full inputs before sampling. Never infer vector order by joins."""
    config = config or TopicConfig()
    reader = read_embeddings if read_embeddings is not None else get_local_embeddings
    bundle = reader(
        dataset=snapshot,
        directory=directory,
        manifest_sha256=manifest_sha256,
        allow_subset=False,
    )
    source = snapshot.data.reset_index(drop=True)
    for name in ("platform", "record_date"):
        if name not in source:
            raise ValueError(f"Prepared dataset is missing {name}")
    if bundle.reviews.record_id.tolist() != source.record_id.tolist():
        raise ValueError("Dataset and embedding order differ")
    frame = source[["record_id", "model_text", "platform", "record_date"]].copy()
    frame["source_row"] = np.arange(len(frame))
    selected, coverage = sample_rows(frame, config)
    from voc.topics.contracts import SELECTION_COLUMNS

    selected = selected[list(SELECTION_COLUMNS)]
    vectors = np.array(bundle.vectors[selected.source_row.to_numpy()], copy=True)
    info = {
        "dataset": bundle.info["dataset"],
        "embedding_release_id": bundle.info["embedding_release_id"],
        "embedding_manifest_sha256": manifest_sha256,
        "profile": bundle.info["profile"],
        "selection_policy": "platform-month-proportional-largest-remainder-v1",
        "sample_seed": config.sample_seed,
        "sample_size": config.sample_size,
        "coverage": coverage,
        "selection_sha256": selection_hash(selected),
        "vectors_sha256": fingerprint(vectors.tolist()),
    }
    result = TopicInput(selected, vectors, info)
    result.validate()
    return result
